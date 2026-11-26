# Written by hand on 2026-11-26 in the shape Django 5.2.18 generates (ADR 0048).

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0004_quote'),
    ]

    operations = [
        migrations.AddField(
            model_name='ratecard',
            name='fees_on_return',
            field=models.BooleanField(default=True, help_text='Untick to charge the booking fee and the loading fee once for a round trip, not on each ride.', verbose_name='charge booking and loading on the ride back'),
        ),
        migrations.AddField(
            model_name='ratecard',
            name='loading_ambulatory',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='loading fee'),
        ),
        migrations.AddField(
            model_name='ratecard',
            name='loading_equipment_delivery',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='loading fee'),
        ),
        migrations.AddField(
            model_name='ratecard',
            name='loading_wheelchair',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='loading fee'),
        ),
        migrations.AddField(
            model_name='ratecard',
            name='per_mile_ambulatory',
            field=models.DecimalField(blank=True, decimal_places=2, help_text="Blank uses the card's per-mile rate.", max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='per mile'),
        ),
        migrations.AddField(
            model_name='ratecard',
            name='per_mile_equipment_delivery',
            field=models.DecimalField(blank=True, decimal_places=2, help_text="Blank uses the card's per-mile rate.", max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='per mile'),
        ),
        migrations.AddField(
            model_name='ratecard',
            name='per_mile_wheelchair',
            field=models.DecimalField(blank=True, decimal_places=2, help_text="Blank uses the card's per-mile rate.", max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='per mile'),
        ),
        migrations.AddField(
            model_name='ratecard',
            name='wait_block_minutes',
            field=models.PositiveIntegerField(default=1, help_text='Wait is charged in blocks of this many minutes, each started block in full. 1 = by the minute.', validators=[django.core.validators.MinValueValidator(1)]),
        ),
        migrations.AddField(
            model_name='ratecard',
            name='wait_rate_ambulatory',
            field=models.DecimalField(blank=True, decimal_places=2, help_text="Per wait block. Blank uses the card's wait rate.", max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='wait rate'),
        ),
        migrations.AddField(
            model_name='ratecard',
            name='wait_rate_wheelchair',
            field=models.DecimalField(blank=True, decimal_places=2, help_text="Per wait block. Blank uses the card's wait rate.", max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='wait rate'),
        ),
        migrations.AlterField(
            model_name='ratecard',
            name='base_ambulatory',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='booking fee (base rate)'),
        ),
        migrations.AlterField(
            model_name='ratecard',
            name='base_equipment_delivery',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='booking fee (base rate)'),
        ),
        migrations.AlterField(
            model_name='ratecard',
            name='base_wheelchair',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='booking fee (base rate)'),
        ),
        migrations.AlterField(
            model_name='ratecard',
            name='wait_per_minute',
            field=models.DecimalField(decimal_places=2, default=0, help_text='Per block (per minute when the block is 1), after the free minutes, on wait-and-return rides.', max_digits=10, validators=[django.core.validators.MinValueValidator(0)], verbose_name='wait rate'),
        ),
    ]
