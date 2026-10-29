---
name: finn-torget
description: Search and manage FINN Torget through native FINN Torget MCP tools.
---

# FINN Torget

Use the typed MCP tools: `search_listings`, `search_categories`, `list_categories`, `list_listings`, `get_listing`, `get_listing_seller`, `get_listings_many`, `search_listings_fulltext`, `get_listing_options`, `update_listing`, `download_image`, `move_image`, `remove_image`, `rotate_image`, `create_listing`, `delete_listing`, `publish_listing`, `saved_searches`, `create_saved_search`, `update_saved_search`, `list_conversations`, `get_conversation`, `search_messages`, `send_message`, and `send_listing_message`. They return structured data. Do not call Python modules as command-line programs.

Use `list_conversations` to read FINN inbox threads, `get_conversation` to read a thread page, and `search_messages` to search recent message pages. Search is bounded and reports whether it reached the end of the available conversation list. These are read-only tools. Do not call `send_message` unless the user separately asks for a specific message to be sent.

Append one or more local images with `update_listing(image_paths=[...])`. Use `move_image`, `remove_image`, and `rotate_image` for existing photos; image positions are one-based.

Download an owner's photo with `download_image(listing_id, image_index, output_path)`. Image positions are one-based; use `1` for the first image. The MCP resolves FINN image URIs through `images.finncdn.no/dynamic/original/` to retrieve the original resolution. The destination directory must already exist, and existing files are never overwritten. If a download fails, check the owner listing's current image URI and retry only after resolving the cause; do not probe with index `0`.

Choose image positions from the current `get_listing` image list. Reuse a local download when its listing ID and image URI still match; otherwise choose a new destination path. An existing destination or out-of-range index does not mean that image retrieval is broken. If an image append gives a timeout or uncertain result, read the listing back and compare its image list before retrying; an automatic repeat can duplicate photos.

Image updates return `outcome`, `listing_id`, `verified`, and `retry_update=false`. `outcome=unconfirmed` does not mean the write failed: inspect by ID and compare the returned expected/uploaded image references. A lost write response followed by matching readback returns `verified=true` and `recovered_after_write_error=true`.

`search_listings(for_rent=null)` or omission searches all ads; `true` selects rent and `false` selects buy. The response echoes `rental_filter` and returns query-wide `rental_facets`. `get_listing` includes raw owner `trade_type` and, by default, rent/buy metadata from an exact singleton public search hit. `trade_type=SELL` can occur on rental ads. Use `for_rent` only with `rental_status_verified=true`; null means unknown, including search misses or ambiguous facets. Public search metadata does not determine owner publication status. Set `include_search_metadata=false` when only owner contents/status are needed.

## Safety

- Do not edit, create, or publish listings, change image order/remove photos, alter saved searches, or send a message unless the user explicitly requests that exact action. The MCP interface does not change delivery settings.
- Paid promotion, paid packages, seller-paid shipping, and paid upgrades are unsupported. If publishing is added, allow only free Standard.
- For new ads, ask about Fiks ferdig versus pickup when the user has not already specified a delivery preference. Never enable Fiks ferdig without an explicitly supplied and verified sender address.
- Safari authentication is imported in memory through the local cookie socket. Never expose cookies, authorization data, write tokens, or raw authenticated requests. If authentication fails, ask the user to sign in to FINN in Safari.
- After a write, rely on returned readback verification. For publication status, use the signed-in owner state by listing ID; the editable form can still say `edit` or `draft` after submission. `list_listings` normalizes status facets to uppercase; use `status_facet="ALL"` when searching by ID across states. If owner status is unknown or unavailable, report unknown and do not retry submission. Do not open the Safari UI to resolve an MCP status mismatch.
- `Til kontroll` means FINN has received the submitted ad and is reviewing it. Treat submission as complete and ready on the seller side; do not republish or use Safari to advance it. If the user explicitly requires the replacement to be publicly visible before deleting an old ad, wait until owner state is `Aktiv`.
- On an uncertain outcome, inspect current state before any retry.

Report whether data came from public search or the signed-in owner view.

Use `search_categories` for literal category/path searches and `list_categories` for paginated browsing. Pass a non-null `creation_category_id` to `create_listing`; top-level search categories cannot be used to create an ad. Condition is optional and is never assumed to be “Som ny”.

## Listing terms

Use the daily rental price the user supplied. Omit optional multi-day, weekly, or monthly prices unless supplied or approved by the user; do not invent discounts. Hygglo requires a replacement value: obtain it from the user or present a concrete estimate for approval before creating the product. Do not treat permission to create an ad as approval of an invented valuation.

When information is missing, ask one concise question at a time using a popup, as requested by this user. Reuse answers already given.

For rentals, include “til leie” or “leies ut” in the title and the daily rental period in the description. The create API has no dedicated rental operation; keep the current submission flow. Any conversion during moderation is unverified and must not be promised.

For condition, use the exact supported label (for example “Pent brukt”), not “Brukt”. Inspect `get_listing_options(listing_id, category_id)` for the actual form choices on an existing draft. After a failed creation, inspect the returned ad ID. Reuse a confirmed draft with `create_listing(draft_id=...)`; never create another ad just to correct the failed draft.

Report `content_verified`, `submission_confirmed`, and `publication_status` separately. A free submission receipt confirms submission, not moderation or public visibility. If publication status is unknown, report it as unknown. Inspect an ad by ID; public search/list membership is not needed to verify saved contents.

## Condition for rentals

For this user's ordinary rental ads, treat the item as used and intended to be functional. Do not offer “Helt ny” or “Må fikses” as routine rental choices, and do not ask about unopened packaging. The relevant wear grades are “Som ny”, “Pent brukt”, and “Godt brukt”. Use “Som ny” when supported by the user’s description; do not assume it by default. Known cosmetic wear does not imply that the item is faulty. Only clarify faults when the user or available information actually mentions a problem; do not claim that an item has been inspected or tested without evidence.

Condition is optional, not a required question. Reuse the user's existing description of wear. On FINN, map a supplied wear description to the matching supported grade, or omit the condition argument if the grade is unknown; do not assume “Pent brukt” merely because the item functions. On Hygglo, include relevant known wear in the description. Do not introduce a separate condition questionnaire just to complete an ad.

Listing tools use `listing_id` for an existing ad, `title` for its title, and `category_id` when selecting a category for creation. Public searches accept `category_ids` for multiple categories. Backend identifiers and payload field names are translated inside the plugin.

Use `update_saved_search(search_id, notifications=[...])` to replace notification settings. Supported values are `web`, `push`, and `email`; an empty list disables notifications.

Delete an ad only when the user explicitly asks to delete that specific listing. Use `delete_listing(listing_id)`; asking to implement deletion support does not authorize deleting an actual listing. FINN deletion is permanent. The tool checks the owner ID before deletion and reads the same ID afterward. Report deletion only when `deleted=true` and `verified=true`. `already_absent=true` means no deletion was performed. For `outcome=unconfirmed`, inspect the listing by ID before any further action; never blindly repeat deletion. Do not report an authentication error or a public-search miss as proof of deletion.

Saved searches use `notifications` for both creation and update. `notifications=[]` disables all notifications; omitting it on creation preserves the default web/push/email selection. Creation verifies the ID, search parameters, name, and notification set. An uncertain creation must be inspected before retrying.


`get_listings_many(listing_ids=[...])` retrieves owner details but returns only id and title by default. Select `include_fields=["description"]` or `["*"]` for descriptions or all supported details. This is a costly convenience operation, not an upstream bulk API: it performs separate detail retrievals for 1–50 IDs, at most four concurrently, without retries. Results preserve input order and contain a listing or a sanitized error for each ID. FINN IDs are strings; `include_search_metadata=false` is the default to avoid an extra public search per ad.


## Fulltext search of own listings

Use `search_listings_fulltext(query, scan_limit=100, limit=50)` to find text in
your own listing titles and descriptions. FINN fulltext defaults to `status_facet=["ACTIVE"]`.
This parameter is an array of unique `ACTIVE`, `DRAFT`, and/or `DISPOSED` values;
multiple statuses mean OR, with one shared `scan_limit` and no duplicate detail reads.
Choose additional statuses only when the task requires them. `ALL` is unsupported
in fulltext search. `list_listings` retains its string parameter, including `"ALL"`; Hygglo scans the owner's public listings. This is a
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

## Rapportering av fulltekstsøk

Ved lesefeil på enkelte annonser: behold vellykkede søkeresultater og hent bare de mislykkede ID-ene på nytt. For egne annonser, bruk `get_listings_many` med de nødvendige feltene, for eksempel `include_fields=["description"]`, og vurder de gjeninnhentede tekstene mot det opprinnelige søkeuttrykket. Ikke gjenta hele fulltekstsøket eller et vellykket søk på den andre plattformen for å reparere slike lesefeil. På Hygglo gjelder eierverktøyet bare egne produkt-ID-er; ikke bruk det som fallback for andre eieres annonser. Rapporter gjenværende lesefeil og eventuelle skanne-/treffgrenser: en vellykket gjeninnhenting gjør ikke automatisk søket komplett.

Når du oppsummerer et fulltekstsøk, oppgi undersøkt antall av totalen og hvilke annonsestatuser søket omfattet. Hvis `complete=false`, si at søket er ufullstendig og forklar begrensningen. Ikke presenter trefflisten som uttømmende.

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
