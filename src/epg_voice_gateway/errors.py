"""Public errors contain stable categories, never upstream URLs or credentials."""


class GatewayError(Exception):
    code = "gateway_unavailable"
    status_code = 503


class CatalogUnavailable(GatewayError):
    code = "catalog_unavailable"


class PlaybackUnavailable(GatewayError):
    code = "playback_unavailable"


class InvalidSource(GatewayError):
    code = "invalid_source"
