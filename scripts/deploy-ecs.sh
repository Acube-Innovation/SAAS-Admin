#!/usr/bin/env bash
# Deploy one build to an ECS environment (used by the CI "deploy" job):
#   1. register new task definition revisions with this commit's images,
#   2. run migrations as a one-off task and stop if they fail,
#   3. roll out the API, worker, beat and admin services and wait until they are stable,
#   4. smoke-test the public URL.
# Needs: aws CLI v2, jq, curl. Values come from the GitHub environment (Terraform output
# github_environment_variables).
set -euo pipefail

: "${ECS_CLUSTER:?}" "${ECS_NAME_PREFIX:?}" "${ECS_PRIVATE_SUBNETS:?}" "${ECS_TASKS_SECURITY_GROUP:?}"
: "${API_IMAGE:?}" "${ADMIN_IMAGE:?}" "${STAGING_URL:?}"

log() { printf '\n==> %s\n' "$*"; }

# Copy the latest revision of a family with a new image; print the new revision's ARN.
register() {
  local family=$1 image=$2 definition
  definition=$(aws ecs describe-task-definition --task-definition "$family" --query taskDefinition --output json \
    | jq --arg image "$image" '.containerDefinitions[0].image = $image
        | del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities,
              .registeredAt, .registeredBy, .deregisteredAt)')
  aws ecs register-task-definition --cli-input-json "$definition" \
    --query taskDefinition.taskDefinitionArn --output text
}

log "Migrations"
migrate_definition=$(register "$ECS_NAME_PREFIX-migrate" "$API_IMAGE")
task=$(aws ecs run-task \
  --cluster "$ECS_CLUSTER" \
  --task-definition "$migrate_definition" \
  --launch-type FARGATE \
  --started-by "deploy-${GITHUB_SHA:-manual}" \
  --network-configuration "awsvpcConfiguration={subnets=[$ECS_PRIVATE_SUBNETS],securityGroups=[$ECS_TASKS_SECURITY_GROUP],assignPublicIp=DISABLED}" \
  --query 'tasks[0].taskArn' --output text)
echo "Task: $task"
aws ecs wait tasks-stopped --cluster "$ECS_CLUSTER" --tasks "$task"
exit_code=$(aws ecs describe-tasks --cluster "$ECS_CLUSTER" --tasks "$task" \
  --query 'tasks[0].containers[0].exitCode' --output text)
aws logs tail "/ecs/$ECS_NAME_PREFIX-migrate" --since 30m --format short | tail -n 40 || true
if [ "$exit_code" != "0" ]; then
  reason=$(aws ecs describe-tasks --cluster "$ECS_CLUSTER" --tasks "$task" --query 'tasks[0].stoppedReason' --output text)
  echo "::error::Migrations failed (exit code $exit_code: $reason). Services were not updated."
  exit 1
fi

log "Rolling out services"
services=()
for service in api worker beat admin; do
  image=$API_IMAGE
  [ "$service" = admin ] && image=$ADMIN_IMAGE
  definition=$(register "$ECS_NAME_PREFIX-$service" "$image")
  aws ecs update-service --cluster "$ECS_CLUSTER" --service "$ECS_NAME_PREFIX-$service" \
    --task-definition "$definition" --query 'service.serviceName' --output text
  services+=("$ECS_NAME_PREFIX-$service")
done
# Fails if a service doesn't settle (the circuit breaker rolls it back to the previous revision).
aws ecs wait services-stable --cluster "$ECS_CLUSTER" --services "${services[@]}"

log "Smoke test $STAGING_URL"
curl -fsS --retry 5 --retry-delay 5 --retry-all-errors "$STAGING_URL/api/health"
echo
curl -fsS --retry 5 --retry-delay 5 --retry-all-errors -o /dev/null -w "GET /login %{http_code}\n" "$STAGING_URL/login"
log "Deployed ${GITHUB_SHA:-this build} to $STAGING_URL"
