"""Public result schemas for the FINN Torget MCP tools.

TypedDict preserves sparse field selection when nested inside RootModel.
The RootModel return annotations avoid the MCP SDK turning absent optional
TypedDict fields into null values.
"""
from typing import Any, Literal
from typing_extensions import NotRequired, Required, TypedDict


class Listing(TypedDict, total=False):
    id: str | int | None
    listing_id: str
    title: str | None
    price: int | float | None
    url: str | None
    subtitle: str | None
    location: str | None
    published: int | str | None
    expires: str | int | None
    images: list[dict[str, Any]]
    condition: Any
    shoe_size: Any
    state: str | None
    state_raw: str | None
    state_source: str | None
    publication_status: str
    publication_status_verified: bool
    category: str | int | None
    trade_type: str | None
    ad_type: int | str | None
    for_rent: bool | None
    rental_status_verified: bool
    rental_status_source: str | None
    description: str
    search_metadata: dict[str, Any]
    snippets: list[dict[str, str]]


class OwnerListing(Listing):
    id: str
    title: str | None
    publication_status: str
    publication_status_verified: bool


class SearchResults(TypedDict):
    total: int | None
    page: int
    page_offset: int
    returned: int
    has_more: bool | None
    next_page: int | None
    next_page_offset: int | None
    listings: list[Listing]
    include_fields: list[str]
    rental_filter: Literal['all', 'rent', 'buy']
    rental_facets: dict[str, int]


class OwnerListings(TypedDict):
    total: int
    listings: list[Listing]
    include_fields: list[str]


class CategoryResults(TypedDict):
    categories: list[dict[str, Any]]
    total: int
    next_offset: NotRequired[int | None]


class ListingRead(TypedDict):
    listing_id: str
    listing: NotRequired[Listing]
    error: NotRequired[dict[str, str]]


class ListingReads(TypedDict):
    mode: Literal['per_listing_requests']
    include_fields: list[str]
    requested: int
    succeeded: int
    failed: int
    results: list[ListingRead]


class FulltextResults(TypedDict):
    query: str
    include_fields: list[str]
    mode: Literal['per_listing_requests']
    total_available: int
    selected: int
    scanned: int
    failed: int
    complete: bool
    matched: int
    returned: int
    has_more_matches: bool
    matches: list[Listing]
    errors: list[dict[str, Any]]
    status_facet: list[Literal['ACTIVE', 'DRAFT', 'DISPOSED']]


class Seller(TypedDict):
    name: str | None
    profile_url: str | None
    type: Literal['private'] | None
    member_since: int | None
    identity_verified: bool | None
    rating_score: float | None
    rating_count: int | None


class ListingSeller(TypedDict):
    listing_id: str
    seller: Seller


class ConversationSummary(TypedDict):
    conversation_id: str | None
    platform: Literal['finn_torget']
    listing_id: str | int | None
    listing_type: str | None
    title: str | None
    partner: str | None
    updated_at: str | None
    unread_count: int
    preview: str | None
    outgoing: bool | None
    status: dict[str, Any] | None


class Conversations(TypedDict):
    conversations: list[ConversationSummary]
    count: int
    next_cursor: str | None
    complete: bool
    unread_only: bool


class Message(TypedDict, total=False):
    id: str
    clientMessageId: str | None
    body: str | None
    sent: str | None
    messageType: str | None
    outgoing: bool
    read: bool
    partnerRead: bool
    attachments: list[dict[str, Any]]


class ConversationMessages(TypedDict):
    conversation_id: str
    messages: list[Message]
    count: int
    older_cursor: str | None


class MessageMatch(TypedDict):
    conversation: ConversationSummary
    match_type: NotRequired[Literal['conversation']]
    message: NotRequired[Message]


class MessageSearch(TypedDict):
    matches: list[MessageMatch]
    count: int
    searched_conversations: int
    complete: bool
    query: str


class MessageSend(TypedDict):
    listing_id: str | None
    conversation_id: str | None
    client_message_id: str
    message_id: str | None
    verified: bool
    outcome: Literal['verified', 'unconfirmed']
    write_attempted: Literal[True]
    write_request_accepted: bool
    request_error: str | None
    verification_error: str | None
    recovered_after_write_error: bool
    retry_send: Literal[False]


class ListingUpdateResult(TypedDict, total=False):
    id: str
    listing_id: Required[str]
    verified: Required[bool]
    outcome: Required[Literal['verified', 'unconfirmed']]
    write_attempted: bool
    write_request_accepted: bool | None
    request_error: str | None
    verification_error: str | None
    recovered_after_write_error: bool
    retry_update: Required[Literal[False]]
    title: str | None
    price: int | float | None
    changed_fields: list[str]
    public_verified: bool
    public_verification_error: str
    image_changes: dict[str, Any]
    image_order: list[str] | None
    expected_image_order: list[str] | None
    error: str


class ListingUpdates(TypedDict):
    mode: Literal['per_listing_requests']
    requested: int
    verified: int
    unconfirmed: int
    verify_public: bool
    results: list[ListingUpdateResult]


class ListingOptions(TypedDict):
    id: NotRequired[str | int]
    category: int | str | None
    source: Literal['supported_condition_catalog', 'live_form_by_id']
    category_applicability_verified: bool
    fields: dict[str, dict[str, Any]]


class DownloadedImage(TypedDict):
    path: str
    bytes: int
    content_type: str
    listing_id: str
    image_index: int
    image_uri: str


class SavedSearch(TypedDict):
    id: int
    name: str | None
    notifications: list[str]
    category: str | None


class SavedSearchChange(TypedDict):
    id: int
    name: str | None
    notifications: list[str]
    verified: Literal[True]


class ListingPublication(TypedDict, total=False):
    id: Required[str]
    url: Required[str]
    state: str | None
    state_raw: str | None
    state_source: str | None
    publication_status: str
    publication_status_verified: bool
    already_submitted: bool
    submission_confirmed: bool
    retry_submission: Literal[False]


class ListingCreation(ListingPublication):
    title: str
    verified: Literal[True]
    content_verified: Literal[True]
    submission_confirmed: bool
    verified_by: Literal['listing_id']
    resumed_draft: bool
    post_submission_content_verified: bool | None
    verification_error: str | None
    retry_creation: Literal[False]


class ListingDeletion(TypedDict):
    listing_id: str
    deleted: bool
    verified: bool
    verified_by: Literal['listing_id'] | None
    retry_deletion: Literal[False]
    already_absent: NotRequired[bool]
    title: NotRequired[str | None]
    delete_request_accepted: NotRequired[bool]
    outcome: NotRequired[Literal['deleted', 'unconfirmed']]
    request_error: NotRequired[str | None]
    verification_error: NotRequired[str | None]
