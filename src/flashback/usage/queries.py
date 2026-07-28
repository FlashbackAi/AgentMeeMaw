INSERT_USAGE_EVENT = """
INSERT INTO usage_events (
    service, feature, provider, model,
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
    units, unit_type, cost_usd, person_id, session_id
) VALUES (
    %(service)s, %(feature)s, %(provider)s, %(model)s,
    %(input_tokens)s, %(output_tokens)s, %(cache_read_tokens)s, %(cache_write_tokens)s,
    %(units)s, %(unit_type)s, %(cost_usd)s, %(person_id)s, %(session_id)s
)
RETURNING id::text
"""
