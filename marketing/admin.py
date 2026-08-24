from django.contrib import admin

from .models import (
    ContentApproval,
    ContentIdea,
    ContentItem,
    ContentPerformance,
    ContentProfile,
    ContentVersion,
    EditorialCalendar,
    MarketingIntegracao,
)

admin.site.register(ContentProfile)
admin.site.register(ContentItem)
admin.site.register(ContentVersion)
admin.site.register(ContentIdea)
admin.site.register(EditorialCalendar)
admin.site.register(ContentApproval)
admin.site.register(ContentPerformance)
admin.site.register(MarketingIntegracao)
