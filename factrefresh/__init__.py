"""fact-refresh: re-reads the sources of a fact graph and reports whether each stored quote is still there.

The refresh (factrefresh.refresh) runs in public CI with no secret and pushes nothing; it writes one report per
graph that follows spec/refresh-report of the graphs repository. The opener (factrefresh.opener) runs on a
maintainer's machine as the organisation's app; it reads reports and opens pull requests and issues, never merges.
"""
