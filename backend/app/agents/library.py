"""Library tool-backed component; physical actions enter via role-checked APIs.

No scheduler or chat mutation subscriptions: expiry is an explicit operator task.
Notifications are staged in the same transaction through the Notification service.
"""
from .base import BaseAgent
from ..library import service


class LibraryAgent(BaseAgent):
    name = 'library_agent'
    description = 'Physical library reservations, handover, returns, fines and recommendations'

    reserve = staticmethod(service.reserve)
    cancel = staticmethod(service.cancel)
    pickup = staticmethod(service.pickup)
    issue_book = staticmethod(service.issue_book)
    request_return = staticmethod(service.request_return)
    confirm_return = staticmethod(service.confirm_return)
    reject_return = staticmethod(service.reject_return)
    pay_fine = staticmethod(service.pay_fine)
    save_book = staticmethod(service.save_book)
    search_catalogue = staticmethod(service.assistant_catalogue_search)
    recommendations = staticmethod(service.recommendations)
