# FINN Torget MCP plugin

Local Codex plugin exposing typed FINN Torget MCP tools over stdio. Tools call the importable Python core in-process. The former CLI entry point has been removed. The MCP surface includes public product search, owner listings, ad read/update/create/publish, image upload/download/rotation/order/removal, saved-search management, and explicit message sending. Tool names are scoped by the `finn_torget` MCP server and use no redundant FINN prefix. Run through `.mcp.json` with `uv` and Python 3.14.

## Conversations

`list_conversations` reads grouped inbox threads with an opaque pagination cursor. `get_conversation` reads a message page, and `search_messages` searches a bounded set of recent threads. These tools do not send messages; `send_message` remains a separate explicit action.

`delete_listing(listing_id)` deletes one explicitly selected owner listing, verifies removal by ID, and never retries an uncertain deletion automatically.

## Update outcomes

`update_listing`, `move_image`, `remove_image`, and `rotate_image` send at most one final listing write and then read the same ID back, including after a lost write response. They return `listing_id`, `verified`, `outcome` (`verified` or `unconfirmed`), `write_attempted`, `write_request_accepted`, `request_error`, `verification_error`, and `retry_update=false`. A matching readback can verify the requested state even when the write response was lost; `recovered_after_write_error=true` identifies this case. An unconfirmed result is not proof that the write failed. Image results include expected and actual references when known; partial uploads report known uploaded references and never commit an incomplete requested image set. Validation failures before any upload or listing write remain ordinary tool errors.


`get_listings_many(listing_ids=[...])` retrieves owner details but returns only id and title by default. Select `include_fields=["description"]` or `["*"]` for descriptions or all supported details. This is a costly convenience operation, not an upstream bulk API: it performs separate detail retrievals for 1–50 IDs, at most four concurrently, without retries. Results preserve input order and contain a listing or a sanitized error for each ID. FINN IDs are strings; `include_search_metadata=false` is the default to avoid an extra public search per ad.


## Fulltext search of own listings

Use `search_listings_fulltext(query, scan_limit=100, limit=50)` to find text in
your own listing titles and descriptions. FINN defaults to `status_facet="ACTIVE"`
(use `ALL` for all states); Hygglo scans the owner's public listings. This is a
costly local scan: each selected ad needs a separate detail retrieval, up to four
concurrently. `scan_limit` caps retrievals at 1–500; `limit` caps returned matches
at 1–100 without reducing scan cost. No title-only prefilter is applied.

Search syntax: whitespace means AND, uppercase `AND`/`OR` are
operators, quotes match phrases, leading `-` excludes, and parentheses group.
Precedence is exclusion, AND, OR. Lowercase and/or are literal words. Matching
uses case-insensitive substrings; no stemming, regex, or wildcard expansion.
Terms can match across title and description; phrases stay within one field.
Inside quotes, backslash escapes quotes/backslashes. Invalid expressions fail
before network work. Limits: 8192 characters, 512 tokens, 32 nesting levels.

Example: `(batteri OR batterier) (ekstra OR "kan lånes")`.

All four tools (`list_listings`, `search_listings`, `get_listings_many`,
`search_listings_fulltext`) return ID and title as their default listing fields.
Hygglo summaries now use `id` (owner product ID), `title`, and optional `url` in
place of `productId`, `name`, and `publicUrl`; the public `listingId` remains a
separate optional field. Fulltext matches keep their existing `listing_id` key.
Use `include_fields=["description"]` for descriptions in detail retrieval and
fulltext search, or select other supported fields named in each tool argument.
Use `include_fields=["*"]` for all supported data already available in that
operation. This is not the raw upstream object. List/public-search responses do
not contain full descriptions: requesting unsupported fields is rejected before
network work, and `*` never hydrates them through extra detail requests.
Hygglo's old `list_listings(include_description=...)` argument is replaced by
this contract; use detail/fulltext tools when descriptions are needed.
Fulltext snippets are now optional via `include_fields=["snippets"]` (also part
of `*`). Field selection does not affect matching, scan coverage, pagination,
error reporting, or existing explicit `include_search_metadata` behavior. `*`
must stand alone; unknown/duplicate fields or an explicitly empty list are
rejected. Defaults remain minimal when `include_fields` is omitted.

`total_available`, `selected`, `scanned`, `failed`, and `complete` report fulltext
scan coverage; `matched`, `returned`, and `has_more_matches` report truncation.
`complete=false` means the answer is not exhaustive. Errors remain per ad.

## Public-search paging and locations

`search_listings.total` is the query-wide `metadata.result_size.match_count`,
not `metadata.num_results` (the latter counts records in one API response).
`total=null` means the API did not supply a reliable total.

Use `page=1, page_offset=0` initially. The response includes `returned`,
`has_more`, `next_page`, and `next_page_offset`. Continue with both returned
values and unchanged search filters. When `limit` cuts off part of an API page,
the continuation stays on that page with a larger offset; no results are skipped.
Each call performs only one public search request. `has_more=null` means the
API did not provide enough paging metadata to establish continuation. FINN may
cap its accessible result pages even when `total` is larger; reaching the last
accessible page does not mean every matching ad was accessible.

`location` accepts dotted numeric FINN codes, not names: Oslo is `0.20061`.
Place names such as `Oslo` are rejected locally with an example of valid input,
before any request. Other syntactically valid but unknown codes may still be
rejected by FINN; do not guess codes. Choosing location fields in `include_fields`
does not set a location filter.


## Sending by listing ID

Use `send_listing_message(listing_id, body)` only for an explicitly authorized
message to that seller. It checks the signed-in contact configuration and Torget
listing type, finds an existing conversation by the listing-specific lookup,
and otherwise creates the conversation with its first message in one request.
No browser automation is needed; existing Safari authentication is reused.
`send_message(conversation_id, body)` sends in an existing conversation. The
account ID is resolved internally; the old `login_id` argument was removed.
Both tools make at most one write and read messages back to verify the exact
client message ID, body, and outgoing direction. Only `verified=true` confirms
sending. `outcome=unconfirmed` may mean the message was sent: inspect the returned
conversation and client message IDs before considering another send. Never retry
an uncertain write automatically. `write_request_accepted` is separate from
readback verification. No raw authenticated responses or credentials are returned.


## Seller profile

`get_listing_seller(listing_id)` reads the seller's visible FINN profile without
sending a message or creating a conversation. It reuses Safari login, checks
that `/messages/api/listing?ids=ad{listing_id}` identifies a Torget listing, and
reads `/messages/api/contact/configuration/{listing_id}`.

The result includes `listing_id` and `seller` with `name` (`ownerName`),
`profile_url` (built from `owner`), `type` (`segment`: PRIVATE -> private; other values -> null), `member_since`
(`ownerMemberSince`), `identity_verified` (`ownerEidVerified`), `rating_score`
(`ownerRatingScore`, decimal comma converted to a number), and `rating_count`
(`ownerRatingCount`). Missing or malformed fields return null, including missing
verification; false remains false and zero ratings remain zero. Anonymous
profiles do not expose an owner profile URL. Ratings are FINN's reported values,
not an independent assessment. Authentication, access, unavailable-listing and
invalid-response errors are distinct; no raw authenticated responses are returned.
