import uuid

from django.conf import settings
from django.db import models

from transportation.addresses import compose
from transportation.models import phone_validator


class FacilityKind(models.TextChoices):
    HOSPITAL = "hospital", "Hospital"
    NURSING_HOME = "nursing_home", "Nursing home / skilled nursing"
    ASSISTED_LIVING = "assisted_living", "Assisted living"
    DIALYSIS = "dialysis", "Dialysis center"
    REHAB = "rehab", "Rehabilitation"
    CLINIC = "clinic", "Clinic / doctor's office"
    OTHER = "other", "Other"


class Stage(models.TextChoices):
    LEAD = "lead", "Lead"
    CONTACTED = "contacted", "Contacted"
    ACTIVE = "active", "Active (sends us rides)"
    LOST = "lost", "Lost / not interested"


class Facility(models.Model):
    """
    A hospital, nursing home, dialysis center or other facility (ADR
    0042): a sales lead first, then an account whose staff book rides
    through the facility portal. Its portal users see every trip linked
    to it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=FacilityKind.choices, default=FacilityKind.OTHER)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.LEAD)

    address = models.TextField(blank=True, help_text="Composed from the parts below.")
    street = models.CharField(max_length=200, blank=True)
    unit = models.CharField("suite", max_length=50, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True, default="TX")
    zip = models.CharField("ZIP", max_length=10, blank=True)
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)

    payer = models.ForeignKey(
        "payers.Payer", null=True, blank=True, on_delete=models.SET_NULL, related_name="facilities",
        help_text="Only if the facility itself pays for rides (a facility contract).",
    )
    account_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="facilities",
        limit_choices_to={"is_staff": True}, help_text="Who at AngelCare looks after this facility.",
    )
    next_follow_up = models.DateField(null=True, blank=True, db_index=True)
    follow_up_note = models.CharField("follow-up about", max_length=200, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "facilities"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if self.street:
            self.address = compose(self.street, self.unit, self.city, self.state, self.zip)
        super().save(*args, **kwargs)


class FacilityContact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=200)
    title = models.CharField(max_length=100, blank=True, help_text="e.g. Discharge planner, Social worker")
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    email = models.EmailField(blank=True)
    is_primary = models.BooleanField("main contact", default=False)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-is_primary", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.title})" if self.title else self.name


class ActivityKind(models.TextChoices):
    CALL = "call", "Call"
    VISIT = "visit", "Visit"
    EMAIL = "email", "Email"
    MEETING = "meeting", "Meeting"
    NOTE = "note", "Note"


class FacilityActivity(models.Model):
    """One call, visit or note: the history of the relationship, newest first. Not edited afterwards."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="activities")
    kind = models.CharField(max_length=20, choices=ActivityKind.choices, default=ActivityKind.NOTE)
    text = models.TextField("what happened")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "activity"
        verbose_name_plural = "activity"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.created_at:%b %d, %Y}"


class FacilityUser(models.Model):
    """
    A facility staff member's portal account (ADR 0042). Created only by
    the office, which emails a set-up link. Signing in takes the password
    and then a code emailed to them.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="facility_user")
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name="portal_users")
    email = models.EmailField(unique=True, help_text="Always stored lowercase. They sign in with it.")
    full_name = models.CharField(max_length=200)
    title = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    invited_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(
        null=True, blank=True, help_text="When they first set a password. Empty = never used."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["facility__name", "full_name"]
        verbose_name = "facility portal user"

    def __str__(self) -> str:
        return f"{self.full_name} ({self.facility})"


class FollowUpDigest(models.Model):
    """The morning email of due follow-ups: one a day at most (ADR 0042)."""

    day = models.DateField(unique=True)
    count = models.PositiveIntegerField()
    sent_at = models.DateTimeField(auto_now_add=True)
