"""Version constants for the Lambda interceptor envelope contract."""

# The output envelope MUST carry this version. Omitting it causes the gateway to
# silently treat the interceptor as a no-op, so the SDK always stamps it.
INTERCEPTOR_OUTPUT_VERSION = "1.0"

# The version the gateway stamps on the input envelope. Used for a soft
# compatibility check (logged warning on mismatch, never rejected).
INTERCEPTOR_INPUT_VERSION = "1.0"
