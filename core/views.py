from django.shortcuts import render
from django.http import Http404
from django.utils.http import url_has_allowed_host_and_scheme

from organizacoes.services import CONTEXT_RESOLVED


def _next_seguro(request):
    raw = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if not raw:
        return None
    if url_has_allowed_host_and_scheme(
        raw,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return raw
    return None


def home(request):
    """Landing pública para anônimos; Home Executiva para autenticados."""
    if not request.user.is_authenticated:
        return render(request, "home.html", {"executiva": None, "tenant_ok": False})

    ctx = getattr(request, "organization_context", None)
    org = getattr(request, "organization", None)
    tenant_ok = ctx == CONTEXT_RESOLVED and org is not None
    executiva = None
    if tenant_ok:
        from core.services.executive_home import montar_home_executiva

        executiva = montar_home_executiva(org, user=request.user)
    return render(
        request,
        "home.html",
        {
            "executiva": executiva,
            "tenant_ok": tenant_ok,
            "view_ativa": "home",
        },
    )


def recusar_media_documentos(request, filename=""):
    """Arquivos tenant-owned não são públicos via MEDIA_URL. Fail closed."""
    raise Http404()
