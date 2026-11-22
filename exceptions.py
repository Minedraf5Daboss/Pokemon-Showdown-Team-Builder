"""Shared search errors, also re-exported by search.py."""

from collections.abc import Iterable


class SearchError(Exception):
    """Base class for invalid team-building requests."""


class RequiredNotInPoolError(SearchError):
    """At least one requested Pokemon is absent from the candidate pool."""

    def __init__(self, missing_names: Iterable[str]):
        self.missing_names = tuple(sorted(set(missing_names)))
        super().__init__(f"Required Pokemon not in pool: {', '.join(self.missing_names)}")


class RequiredAlsoExcludedError(SearchError):
    """A Pokemon is both required and excluded by the request."""

    def __init__(self, names: Iterable[str]):
        self.names = tuple(sorted(set(names)))
        super().__init__(f"Required Pokemon also excluded: {', '.join(self.names)}")


class PoolTooSmallError(SearchError):
    """The pool cannot supply the requested number of distinct team members."""

    def __init__(self, available: int, required: int):
        self.available = available
        self.required = required
        super().__init__(f"Pool has {available} Pokemon; a team requires {required}")


# Keep the earlier exercise imports pointing to the same exception classes.
TeamBuilderError = SearchError
RequiredPokemonNotInPoolError = RequiredNotInPoolError
RequiredPokemonExcludedError = RequiredAlsoExcludedError
