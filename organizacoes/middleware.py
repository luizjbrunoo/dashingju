from organizacoes.services import resolver_organization


class TenantContextMiddleware:
    """Anexa request.organization e request.organization_context.

    Apenas resolve contexto. Não autoriza, não bloqueia, não redireciona.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        organization, context = resolver_organization(getattr(request, "user", None))
        request.organization = organization
        request.organization_context = context
        return self.get_response(request)
