"""eval-llm: the large-model service.

Exposes domain endpoints (/generate-cases, /judge) over HTTP and isolates all
provider selection + API keys here. eval-api reaches this service via HTTP; no
other service holds model credentials.
"""
