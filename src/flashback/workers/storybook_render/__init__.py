"""storybook_render worker — Python-owned storybook rendering (spec 2026-06-29).

Consumes the ``storybook_render`` SQS queue (trigger-only payloads; Postgres
authoritative), curates + assembles + renders the book, uploads via
Node-minted presigned URLs, and announces completion with a transactional
``storybook_render_complete`` NOTIFY.
"""
