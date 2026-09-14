"""Provisioning — get a GPU running an engine, and remember where it is.

`admitperf infra up` starts an engine somewhere and writes its URL to a session
file; `admitperf run` reads that file. The two commands are separate because
bringing a model up takes minutes and you will run many policies against one
deployment.
"""

from admitperf.infra.session import Session, SessionStore

__all__ = ["Session", "SessionStore"]
