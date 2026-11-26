# Written by hand on 2026-11-26 in the shape Django 5.2.18 generates (ADR 0047).

import django.core.validators
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0003_remove_ratecard_base_stretcher'),
        ('transportation', '0007_alter_triprequest_requested_datetime_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Quote',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('miles', models.DecimalField(blank=True, decimal_places=2, help_text='One-way miles the price was worked out from.', max_digits=7, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ('wait_minutes', models.PositiveIntegerField(default=0, help_text='Expected wait at the destination, on wait-and-return rides.')),
                ('lines', models.JSONField(blank=True, default=list, help_text='How the amount was calculated.')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10, validators=[django.core.validators.MinValueValidator(0)])),
                ('sent_to', models.EmailField(max_length=254)),
                ('delivered', models.BooleanField(default=True, help_text="Off when the email couldn't be sent (see the email log).")),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('rate_card', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='quotes', to='billing.ratecard')),
                ('sent_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('trip_request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='quotes', to='transportation.triprequest')),
            ],
            options={
                'verbose_name': 'quote',
                'ordering': ['-created_at'],
            },
        ),
    ]
