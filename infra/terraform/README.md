# Infrastructure (Terraform, AWS ap-south-1)

Staging runs the API, Celery worker, Celery beat and the super admin console on ECS Fargate behind
one HTTPS load balancer. GitHub Actions builds the images, runs migrations and rolls out every merge
to `main` once all tests pass.

```
                 Internet
                    │ 443 (80 redirects)
          ┌─────────▼──────────┐   ACM certificate for admin_domain
          │ Application LB     │   /api/*  → API target group
          │ (public subnets)   │   else    → admin target group
          └───┬────────────┬───┘
              │            │
   ┌──────────▼──┐   ┌─────▼──────┐   ┌────────────┐ ┌────────────┐  private subnets
   │ api :8000   │   │ admin :3000│   │ worker     │ │ beat (x1)  │  (egress via NAT;
   │ gunicorn    │   │ Next.js    │   │ celery     │ │ celery     │   S3 via endpoint)
   └──────┬──────┘   └────────────┘   └─────┬──────┘ └─────┬──────┘
          │                                 │              │
   ┌──────▼─────────────────────────────────▼──────────────▼──────┐  data subnets
   │ RDS PostgreSQL 16 (TLS forced)    ElastiCache Redis 7 (TLS)   │  (no internet route)
   └──────────────────────────────────────────────────────────────┘
   S3 (files) · SES (email) · Secrets Manager · CloudWatch Logs (/ecs/<service>)
```

| Path | What it is |
|---|---|
| `bootstrap/` | One-off per AWS account: Terraform state bucket, ECR repositories, GitHub OIDC deploy role. Local state. |
| `environments/staging/` | The staging environment. State in S3 (`staging/terraform.tfstate`). |
| `environments/staging/tests/` | `terraform test` checks with a mocked AWS provider (run in CI, no credentials). |
| `modules/ecs_service/` | Task definition, service and log group for one container. |
| `../../scripts/deploy-ecs.sh` | What CI runs to deploy: migrate, roll out, smoke test. |

## How it fits together

- **One host for the console and its API.** The load balancer sends `/api/*` to Django and
  everything else to Next.js, so the session cookie and CSRF work same-origin, as they do locally
  through the Next.js proxy. The client portal gets its own host rule when it is deployed.
- **Database roles.** RDS creates an admin user (`dbadmin`, password kept by RDS in Secrets
  Manager). Before every rollout, CI runs the `migrate` task: `manage.py ensure_db_roles` creates or
  updates `app_owner` and `app_user` (both `NOBYPASSRLS`, as in `infra/docker/postgres/init-roles.sh`),
  then `migrate` runs as `app_owner`. The API, worker and beat connect as `app_user` and never see
  the owner or admin password.
- **Secrets.** `<project>-<env>/app` in Secrets Manager holds the Django secret key, both role
  passwords and the Redis URL (with its auth token). ECS injects them as environment variables at
  start. Terraform generates them, so they are also in the Terraform state: the state bucket is
  encrypted, private and TLS-only, so keep access to it limited.
- **No stored AWS keys.** Tasks use IAM roles (S3, SES). GitHub Actions assumes a deploy role
  through OIDC, allowed only for jobs in the `staging` GitHub environment of this repository.
- **Deploys don't fight Terraform.** CI registers a new task definition revision per commit;
  services ignore task definition changes in Terraform. A Terraform change to environment variables
  or sizes is picked up by the next deploy (it copies the latest revision and swaps the image).
- **Rollback.** Each service has the ECS deployment circuit breaker with rollback. If migrations
  fail, the deploy stops before any service is updated. To go back to an older commit, re-run its
  deploy job in GitHub Actions (migrations are forward-only; write a new migration to undo one).

## Cost (staging, approximate)

About USD 120–180 per month at the defaults: NAT gateway (~35), load balancer (~20), RDS
db.t4g.micro (~15), ElastiCache cache.t4g.micro (~12), Fargate for four small services (~45),
plus logs, Secrets Manager and data transfer. `nat_gateway_per_az = true` and `db_multi_az = true`
add resilience and cost.

## First deploy

Prerequisites: Terraform ≥ 1.10, AWS CLI v2, an AWS profile with administrator access for the
first apply, and a domain you control.

1. **Bootstrap the account** (once):
   ```bash
   cd infra/terraform/bootstrap
   terraform init && terraform apply
   ```
   Note the outputs: `state_bucket`, `ecr_repositories`, `github_deploy_roles`.

2. **Configure staging:**
   ```bash
   cd ../environments/staging
   cp backend.hcl.example backend.hcl            # set bucket = <state_bucket>
   cp terraform.tfvars.example terraform.tfvars  # domains, route53_zone_id, deploy role name
   terraform init -backend-config=backend.hcl
   ```

3. **Certificate.**
   - *With Route 53* (`route53_zone_id` set): nothing to do. Terraform validates the certificate
     and creates the admin and DKIM records.
   - *Other DNS provider:* create the certificate first, add its validation record, then continue:
     ```bash
     terraform apply -target=aws_acm_certificate.admin
     terraform console <<< 'aws_acm_certificate.admin.domain_validation_options'
     ```
     Add the CNAME shown at your DNS provider and wait until it resolves.

4. **Apply:** `terraform apply` (about 15 minutes, mostly RDS and ElastiCache). The ECS services
   start with the image tag `bootstrap`, which doesn't exist yet. They show failed tasks until the
   first deploy in step 6. That is expected.

5. **DNS and email** (other DNS provider only): add the records in the `dns_records_to_add` output
   (admin CNAME to the load balancer, three SES DKIM CNAMEs). New AWS accounts start in the SES
   *sandbox*, which only delivers to verified addresses. Request production access in the SES
   console before inviting real clients.

6. **Turn on deploys.** In GitHub, open SAAS-Admin, then Settings > Environments, create `staging`,
   and limit it to the `main` branch. Add these environment **variables** (none are secrets):
   - every key/value from `terraform output github_environment_variables`
   - `AWS_DEPLOY_ROLE_ARN`: bootstrap output `github_deploy_roles.staging.arn`
   - `STAGING_DEPLOY` = `true`

   Then merge to `main` (or re-run the latest CI run on `main`). The `Deploy to staging` job builds
   both images, runs migrations, rolls out the services and checks `/api/health` and `/login`.

7. **Create the first super admin** with ECS Exec (password is read from the prompt, not argv):
   ```bash
   CLUSTER=schedule-saas-staging
   TASK=$(aws ecs list-tasks --cluster $CLUSTER --service-name $CLUSTER-api --query 'taskArns[0]' --output text)
   aws ecs execute-command --cluster $CLUSTER --task $TASK --container app --interactive \
     --command "python manage.py createplatformuser --email you@acube.co --full-name 'Your Name' --role super_admin"
   ```
   (Needs the Session Manager plugin for the AWS CLI.) Sign in at `admin_url` and set up 2FA.

## Day to day

| Task | How |
|---|---|
| Deploy | Merge to `main`. CI runs backend, frontend, e2e and Terraform checks, then deploys. |
| Logs | CloudWatch log groups `/ecs/schedule-saas-staging-<api\|worker\|beat\|admin\|migrate>`, or `aws logs tail /ecs/schedule-saas-staging-api --follow`. |
| Management command | `aws ecs execute-command … --command "python manage.py <command>"` as in step 7. |
| Change sizes or settings | Edit variables or `ecs.tf`, run `terraform apply`, then deploy (merge or re-run). |
| Scale | `api_desired_count`, `worker_desired_count`, `admin_desired_count`. Beat always runs one task. |
| Database access | No public endpoint. Use ECS Exec into an API task and `python manage.py dbshell` (needs `psql`, not in the image), or a temporary bastion. |

## Checks without AWS

```bash
cd infra/terraform
terraform fmt -check -recursive
terraform -chdir=bootstrap init -backend=false && terraform -chdir=bootstrap validate
terraform -chdir=environments/staging init -backend=false && terraform -chdir=environments/staging validate
terraform -chdir=environments/staging test     # mocked provider: routing, secrets, roles, DNS modes
tflint --init --config "$PWD/.tflint.hcl"
for d in bootstrap environments/staging modules/ecs_service; do tflint --config "$PWD/.tflint.hcl" --chdir $d; done
```

CI runs the same commands in the `Terraform` job.
