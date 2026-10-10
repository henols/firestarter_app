try:
    # Written by setuptools-scm at install or build time, from the git tag.
    from firestarter._version import __version__
except ImportError:  # a source tree that was never installed
    __version__ = "0.0.0.dev0"
