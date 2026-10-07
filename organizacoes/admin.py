from django.contrib import admin

from organizacoes.models import Membership, Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "created_by", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "slug")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("created_by",)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "status", "created_at")
    list_filter = ("role", "status")
    search_fields = ("user__username", "organization__name", "organization__slug")
    readonly_fields = ("created_at",)
    raw_id_fields = ("user", "organization")
