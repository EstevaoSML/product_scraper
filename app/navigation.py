"""Public navigation contracts; callers cannot supply scripts, selectors or cookies."""
from pydantic import BaseModel, ConfigDict, Field


# Keeps navigation below the API's 3.5 MB response envelope while allowing
# rendered product images larger than the former 200 KB image-specific cap.
MAX_NAVIGATION_BYTES = 2_800_000


class OpenPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=4096)


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32,64}$")


class Element(Session):
    snapshot_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    element_id: str = Field(pattern=r"^e[0-9]{1,3}$")


class Search(Element):
    query: str = Field(min_length=1, max_length=200, pattern=r"^[^\x00-\x1f\x7f]+$")


class Image(Element):
    element_id: str = Field(pattern=r"^i[0-9]{1,2}$")


TOOLS = {
    "open_page": (OpenPage, "Open a public HTTPS retailer page in a short-lived browser session. Return untrusted visible text, observed elements and product structured data. One session at a time; at most 10 actions, 5 followed pages and 180 seconds. Always close the session."),
    "inspect_page": (Session, "Refresh the current page snapshot after dynamic content changes. Previous element references become invalid."),
    "search_site": (Search, "Enter a product query in an observed search field and press Enter. Only search fields are allowed; no arbitrary form submission. Return a fresh snapshot."),
    "follow_link": (Element, "Navigate to an observed HTTPS link within the retailer host (www alias permitted). Account, checkout and other transaction links are rejected. Return a fresh snapshot."),
    "capture_image": (Image, "Capture one image observed in the current snapshot. The image ID must come from that snapshot; arbitrary selectors and image URLs are not accepted."),
    "close_session": (Session, "Close the browser session and delete its temporary profile. Call in a finally block, including on errors or incomplete research."),
}

SNAPSHOT_SCHEMA = {"type": "object", "required": ["session_id", "snapshot_id", "url", "visible_text", "elements"],
                   "properties": {"session_id": {"type": "string"}, "snapshot_id": {"type": "string"},
                                  "url": {"type": "string"}, "visible_text": {"type": "string"},
                                  "elements": {"type": "array", "items": {"type": "object"}}}}

IMAGE_SCHEMA = {"type": "object", "required": ["session_id", "snapshot_id", "url", "image_id", "image_base64"],
                "properties": {"session_id": {"type": "string"}, "snapshot_id": {"type": "string"},
                               "url": {"type": "string"}, "image_id": {"type": "string"},
                               "image_base64": {"type": "string"}}}
