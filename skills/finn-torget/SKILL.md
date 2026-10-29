---
name: finn-torget
description: Search FINN Torget, inspect sellers, manage account listings and saved searches, and read or send messages through native MCP tools.
---

# FINN Torget

Use the typed FINN Torget MCP tools and their structured results. Do not run the plugin's Python modules as command-line programs. This plugin supports Torget listings, not other FINN marketplaces.

## Authentication and authorization

Public search is available without login. Account operations use the existing Safari login through a local cookie service. If authentication is required, ask the user to sign in to FINN in Safari. Never expose cookies, authorization headers, write tokens, or raw authenticated requests.

Read and draft within the user's requested scope. Create, edit, publish, or delete listings, change photos or saved searches, and send messages only when the user explicitly requests the specific action. A request to implement or test a capability does not authorize changes to live account data.

Paid promotions, paid packages, seller-paid shipping, and paid upgrades are unsupported. Publishing supports free Standard only. The tools do not change delivery settings.

## Public search and categories

Use `search_listings` for public listings, `search_categories` for literal category or category-path matches, and `list_categories` for paginated category browsing.

- `for_rent` omitted or null searches all listings; true selects rentals and false selects items for sale.
- `location` accepts dotted numeric FINN codes, not place names. Oslo is `0.20061`. Use verified codes for other locations rather than guessing.
- Start with `page=1, page_offset=0`. Continue with both `next_page` and `next_page_offset`, keeping filters unchanged. A result limit can leave the continuation on the same API page.
- `total` is the query-wide match count, not the number returned on one page. Null means no reliable total was supplied. FINN may limit accessible pages below that total; report this coverage limit.
- `has_more=null` means continuation could not be established from the API metadata.

Search results do not include full descriptions. Selecting additional fields does not fetch missing listing details.

## Owner listings and field selection

Use `list_listings` for the signed-in user's inventory. When IDs are known, use `get_listing` for one or `get_listings_many` for several rather than searching again. FINN listing IDs are strings.

`list_listings`, `search_listings`, `get_listings_many`, and `search_listings_fulltext` return ID and title by default. Request additional supported fields through `include_fields`, such as `["description"]` for owner details or `["*"]` for all fields available to that operation. `*` must stand alone. Unsupported fields, duplicates, and explicitly empty selections are rejected. Field selection does not change search matching or coverage.

`get_listings_many` accepts 1–50 IDs and performs separate detail retrievals, up to four concurrently. Results preserve input order and include per-listing errors. It is not an upstream bulk API and does not retry failed reads automatically. `include_search_metadata=false` is its default, avoiding an extra public search per listing.

`get_listing` can supplement owner details with rental metadata from an exact public-search match. Set `include_search_metadata=false` when only owner contents or status are needed. Do not infer rental status from `trade_type=SELL`: this value can also occur on rentals. Treat `for_rent` as established only when `rental_status_verified=true`; null means unknown. Public search does not establish owner publication status.

State whether information came from public search, the signed-in owner view, or the seller profile.

## Fulltext search of owner listings

Use `search_listings_fulltext` to match owner listing titles and descriptions. This retrieves each selected listing's details, up to four concurrently, rather than performing an upstream fulltext query.

- `status_facet` is an array of unique `ACTIVE`, `DRAFT`, and/or `DISPOSED` values; it defaults to `["ACTIVE"]`. Multiple statuses mean OR. `ALL` is unsupported here, although `list_listings` accepts `status_facet="ALL"`.
- `scan_limit` caps detail retrievals at 1–500 across all selected statuses. `limit` caps returned matches at 1–100 without reducing scan cost. No listing is retrieved twice within one search.
- Whitespace and uppercase `AND` mean conjunction; uppercase `OR`, quoted phrases, leading `-` exclusions, and parentheses are supported. Precedence is exclusion, AND, OR. Lowercase operators are literal words.
- Matching uses case-insensitive substrings, without stemming, regex, or wildcards. Terms may match across fields; a phrase must match within one field. Inside quotes, backslash escapes quotes and backslashes.
- Query limits are 8192 characters, 512 tokens, and 32 nesting levels. Invalid expressions fail before network requests.
- Request `include_fields=["snippets"]` for excerpts or `["description"]` for full descriptions. Field selection does not affect matching.

Report the statuses searched and the number scanned against `total_available`. `complete=false` means the search is incomplete; `has_more_matches` separately indicates truncated matches. Preserve successful results after partial failures. Retrieve only failed owner IDs with `get_listings_many`, evaluate the recovered text against the original query, and report any remaining failures or coverage limits. Recovering failed reads does not automatically make the original search complete.

## Seller profiles

Use `get_listing_seller(listing_id)` for visible seller information. It does not send a message or create a conversation.

The `seller` object contains `name`, `profile_url`, `type`, `member_since`, `identity_verified`, `rating_score`, and `rating_count`. Missing or malformed fields are null; false verification and zero ratings remain distinct from missing values. Unknown seller types are null, and anonymous profiles omit the profile URL. Present ratings and verification as FINN's reported information, not an independent endorsement.

## Creating and updating listings

Use `get_listing_options` for supported category and condition choices. Creation requires a non-null `creation_category_id` from category metadata; top-level search categories cannot be used to create listings.

Use `create_listing`, `update_listing`, or `update_listings_many` for the requested changes. Supply agreed prices and terms. Omit optional prices or discounts that the user has not supplied or approved. For rental ads, make the rental purpose and price period explicit; do not promise that FINN moderation will convert the listing into a particular rental format.

Condition is optional. Map the user's description to a supported label, or omit it when unknown. Do not infer a condition grade from functionality or assume that rental equipment has a particular wear level. Use labels returned by `get_listing_options`, such as `Pent brukt`, rather than unsupported labels such as `Brukt`.

After failed creation, inspect any returned listing ID. Reuse a confirmed draft with `create_listing(draft_id=...)` rather than creating another listing to correct it.

Report `content_verified`, `submission_confirmed`, and `publication_status` separately. A submission receipt confirms receipt by FINN, not public visibility. Use owner state by listing ID to check publication; an editable form can still say `edit` or `draft` after submission. `list_listings` normalizes status filters to uppercase.

`Til kontroll` means FINN is reviewing the submitted listing. Do not republish it or use the Safari UI to advance moderation. If the user requires a replacement to be publicly visible before deleting the old listing, wait for owner status `Aktiv`. If status is unknown, report it as unknown and do not repeat submission to resolve the uncertainty.

## Photos

Use the current image list from `get_listing` to choose positions. Image positions are one-based.

- Append local images with `update_listing(image_paths=[...])`.
- Reorder, remove, or rotate existing images with `move_image`, `remove_image`, and `rotate_image`.
- Download an owner's original image with `download_image(listing_id, image_index, output_path)`. The destination directory must exist; existing files are not overwritten. Reuse a previous download only when its listing ID and image URI still match.

An out-of-range index or existing destination does not indicate a broken download service. On retrieval failure, inspect the current image URI before retrying. After an uncertain upload, compare the listing's image list with expected and uploaded references before considering another attempt; repeating an upload can duplicate photos.

## Saved searches

Use `saved_searches` to read saved searches and their IDs. Use `create_saved_search` to create one and `update_saved_search(search_id, notifications=[...])` to replace notification settings.

Supported notification values are `web`, `push`, and `email`. An empty list disables notifications. Omitting notifications during creation preserves the default selection. Creation verifies the saved ID, query parameters, name, and notification set. Inspect an uncertain creation before considering another attempt.

## Conversations and sending messages

Use `list_conversations` to browse grouped inbox threads, `get_conversation` to read a thread, and `search_messages` to search a bounded set of recent threads and messages. Respect pagination and report incomplete search coverage. These tools do not send messages.

Use `send_listing_message(listing_id, body)` to contact a seller from a listing. It reuses an existing conversation when available or creates a conversation with its first message. Use `send_message(conversation_id, body)` for an existing conversation. Both resolve the signed-in account ID internally and require an explicitly authorized message.

Both sending tools perform at most one write and verify the message by reading back its client message ID, body, and outgoing direction. Only `verified=true` confirms sending. `write_request_accepted` alone is not confirmation. For `outcome=unconfirmed`, inspect the returned conversation and client message IDs; do not automatically send again.

## Write outcomes and deletion

For listing and photo updates, rely on returned readback verification. `outcome=unconfirmed` can mean the change succeeded but could not be confirmed. Inspect current state by ID before considering another write. `recovered_after_write_error=true` indicates that readback verified the change after a lost write response. Partial image uploads do not commit an incomplete requested image set. Do not use public-search absence or a UI mismatch as proof that a write failed.

Use `delete_listing(listing_id)` only for a specifically authorized deletion. FINN deletion is permanent. Report deletion only when `deleted=true` and `verified=true`; `already_absent=true` means no deletion was performed. After an unconfirmed deletion, inspect the same listing ID before any further action. Authentication failure or absence from public search does not prove deletion.
