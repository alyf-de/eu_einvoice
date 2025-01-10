import base64
from pathlib import Path
from typing import Literal

PROFILE_TO_LOGO = {
	"BASIC": "public/img/fx-basic.png",
	"EN 16931": "public/img/fx-en16931.png",
	"EXTENDED": "public/img/fx-extended.png",
}


def get_einvoice_logo(profile: Literal["BASIC", "EN 16931", "EXTENDED"]) -> str | None:
	"""Return the logo for the given profile, as a base64 encoded data URL."""
	if profile not in PROFILE_TO_LOGO:
		return None

	logo_path = Path(__file__).parent / PROFILE_TO_LOGO.get(profile)
	logo_bytes = logo_path.read_bytes()
	logo_base64 = base64.b64encode(logo_bytes).decode("utf-8")

	return f"data:image/png;base64,{logo_base64}"
