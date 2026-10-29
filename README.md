# FINN Torget for Codex

Search FINN Torget, manage your listings, and contact sellers from Codex. This plugin runs locally and uses your existing FINN login in Safari for account features.

This is an unofficial integration and is not affiliated with FINN.

## Features

- **Find items:** Search public listings by keyword, price, category, location, and whether an item is for sale or rent.
- **Check sellers:** View a seller's name, profile link, membership year, identity verification, and ratings when FINN provides them.
- **Manage your ads:** Read, create, edit, publish, and delete your own Torget listings. Search across their titles and descriptions or update several listings at once.
- **Manage photos:** Upload, download, reorder, rotate, and remove listing photos.
- **Use messages:** Read conversations, search recent messages, reply, or start a conversation from a listing.
- **Save searches:** Create saved searches and change their notification settings.

## Requirements

- Codex with plugin support.
- [uv](https://docs.astral.sh/uv/) available in the `PATH` used by Codex.
- Python available through uv. The current version has been tested with Python 3.13.
- A FINN account signed in through Safari for private listings, messages, seller profiles, and other account features.
- **CurlSafariServer** installed and running for Safari authentication. This is a separate dependency and is not included in this repository. It must be able to read Safari cookies; macOS may require Full Disk Access for the server app.

### Platform support

macOS is the tested platform. Authenticated features currently require macOS because they rely on Safari and CurlSafariServer. Signing in through another browser does not provide authentication to this plugin.

Public listing searches do not require a FINN login or the Safari cookie service. They may work on other operating systems, but Linux and Windows have not been tested. The bundled launcher uses a POSIX shell (`sh`); Windows would also need a compatible shell or an adapted launcher. Full account support on other platforms requires an alternative authentication method.

## Installation

Clone the repository and register its local plugin marketplace:

```sh
git clone https://github.com/hpbieker/codex-plugin-finn-torget.git
cd codex-plugin-finn-torget
codex plugin marketplace add .
codex plugin add codex-plugin-finn-torget@finn-torget-local
```

The launcher finds `uv` through `PATH` and creates a Python environment and installs dependencies on first use. No fixed installation directory is required.

For authenticated features, sign in to FINN in Safari and start CurlSafariServer. Its default socket is `/private/tmp/curl-safari.sock`; set `CURL_SAFARI_SOCKET_PATH` in the server environment if your installation uses another location.

## Usage

Ask Codex in natural language. For example:

> Search for used furniture in a specified area and price range.

> Show the seller profile and ratings for a listing.

> Search listing titles and descriptions for a keyword.

> Draft a question about an item, then send it when approved.

> Update the price or description of a selected listing.

> Create a saved search with email notifications.

Reading information and drafting text do not send messages or change listings. The plugin's instructions require an explicit request before sending a message or changing account data. These actions use the permissions of your signed-in FINN account.

## Privacy and account access

The MCP server runs locally and imports domain-scoped Safari cookies through the local cookie service. It keeps those cookies in memory rather than writing them to disk. The plugin does not ask for your FINN password.

Requests go to FINN using your account where authentication is required. Information returned by a tool is made available to the Codex conversation, including private messages when you ask to read them.

## Limitations

- Only FINN **Torget** is supported, not FINN Jobs, property, or vehicle listings.
- The integration uses FINN's web endpoints. Changes to FINN may require a plugin update.
- Paid promotions, paid packages, and paid publishing upgrades are unsupported. Publishing supports free Standard only.
- Search results and message searches can be limited or paginated. A partial search is not a complete account history.
- Seller information can be missing. Ratings and identity verification are reported as supplied by FINN.
- Changes and messages are checked by reading data back where supported. An unconfirmed outcome can still mean the action succeeded; check the current state before trying again.
- Deleting a listing is permanent.

## Development

The plugin exposes an MCP server over stdio. Its entry point is `finn_torget_mcp.py`; shared listing helpers are in `core/`. Agent usage instructions are in [`skills/finn-torget/SKILL.md`](skills/finn-torget/SKILL.md).

To install dependencies and run the tests:

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

The automated tests use simulated API responses and do not send messages or change FINN listings.
