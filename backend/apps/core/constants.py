"""Fixed choice lists shared across apps."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class Language(models.TextChoices):
    MALAYALAM = "ml", _("Malayalam")
    ENGLISH = "en", _("English")
    HINDI = "hi", _("Hindi")
    TAMIL = "ta", _("Tamil")


# Indian states and union territories: (code, name, GST state code).
STATES = [
    ("AN", "Andaman and Nicobar Islands", "35"),
    ("AP", "Andhra Pradesh", "37"),
    ("AR", "Arunachal Pradesh", "12"),
    ("AS", "Assam", "18"),
    ("BR", "Bihar", "10"),
    ("CH", "Chandigarh", "04"),
    ("CG", "Chhattisgarh", "22"),
    ("DH", "Dadra and Nagar Haveli and Daman and Diu", "26"),
    ("DL", "Delhi", "07"),
    ("GA", "Goa", "30"),
    ("GJ", "Gujarat", "24"),
    ("HR", "Haryana", "06"),
    ("HP", "Himachal Pradesh", "02"),
    ("JK", "Jammu and Kashmir", "01"),
    ("JH", "Jharkhand", "20"),
    ("KA", "Karnataka", "29"),
    ("KL", "Kerala", "32"),
    ("LA", "Ladakh", "38"),
    ("LD", "Lakshadweep", "31"),
    ("MP", "Madhya Pradesh", "23"),
    ("MH", "Maharashtra", "27"),
    ("MN", "Manipur", "14"),
    ("ML", "Meghalaya", "17"),
    ("MZ", "Mizoram", "15"),
    ("NL", "Nagaland", "13"),
    ("OD", "Odisha", "21"),
    ("PY", "Puducherry", "34"),
    ("PB", "Punjab", "03"),
    ("RJ", "Rajasthan", "08"),
    ("SK", "Sikkim", "11"),
    ("TN", "Tamil Nadu", "33"),
    ("TS", "Telangana", "36"),
    ("TR", "Tripura", "16"),
    ("UP", "Uttar Pradesh", "09"),
    ("UK", "Uttarakhand", "05"),
    ("WB", "West Bengal", "19"),
]
STATE_CHOICES = [(code, name) for code, name, _gst in STATES]
GST_STATE_CODES = {code: gst for code, _name, gst in STATES}
