from django.db import models

# Create your models here.
from django.db import models
from django.contrib.auth.models import User

class ResumeAnalysis(models.Model):
    DECISION_CHOICES = [
    #  What the Backend/Chef Sees   |   What the User/Customer Sees
    (  'GO'                        ,   '🟢 GO'                      ),
    (  'NO-GO'                     ,   '🚨 NO-GO'                   ),
    (  'MARGINAL'                  ,   '⚠️ MARGINAL'                 ),
]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='analyses')
    filename = models.CharField(max_length=255)
    job_description = models.TextField(blank=True, null=True)
    match_score = models.IntegerField(default=0)  # Extracted out of 100
    decision = models.CharField(max_length=15, choices=DECISION_CHOICES, default='MARGINAL')
    report_markdown = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.filename} ({self.created_at.strftime('%Y-%m-%d')})"