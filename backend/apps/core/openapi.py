"""OpenAPI schema post-processing for the generated TypeScript client."""


def require_response_fields(result, generator, request, public):
    """
    DRF serializers always output every declared field, so in response components (not the
    ...Request / Patched... input components) mark every property as required. The generated
    client then types returned fields as present instead of optional.
    """
    for name, component in result.get("components", {}).get("schemas", {}).items():
        if name.endswith("Request") or name.startswith("Patched"):
            continue
        properties = component.get("properties")
        if properties:
            component["required"] = sorted(properties)
    return result
