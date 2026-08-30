from __future__ import annotations

from agentos.gateway.websocket import SubscriptionManager


def test_subscription_manager_cleans_up_empty_message_subs_on_unsubscribe() -> None:
    sm = SubscriptionManager()

    sm.subscribe_messages("conn1", "session_a")
    sm.subscribe_messages("conn2", "session_a")
    sm.subscribe_messages("conn1", "session_b")

    assert sm.get_message_subscribers("session_a") == {"conn1", "conn2"}
    assert sm.get_message_subscribers("session_b") == {"conn1"}
    assert "session_a" in sm._message_subs
    assert "session_b" in sm._message_subs

    # Unsubscribe conn1 from session_b -> session_b should be deleted from dict
    sm.unsubscribe_messages("conn1", "session_b")
    assert "session_b" not in sm._message_subs
    assert sm.get_message_subscribers("session_b") == set()

    # Unsubscribe conn1 from session_a -> conn2 still subscribed, key remains
    sm.unsubscribe_messages("conn1", "session_a")
    assert "session_a" in sm._message_subs
    assert sm.get_message_subscribers("session_a") == {"conn2"}

    # Unsubscribe conn2 from session_a -> session_a should be deleted from dict
    sm.unsubscribe_messages("conn2", "session_a")
    assert "session_a" not in sm._message_subs
    assert sm._message_subs == {}


def test_subscription_manager_cleans_up_empty_message_subs_on_remove_connection() -> None:
    sm = SubscriptionManager()

    sm.subscribe_sessions("conn1")
    sm.subscribe_messages("conn1", "session_1")
    sm.subscribe_messages("conn1", "session_2")
    sm.subscribe_messages("conn2", "session_2")
    sm.subscribe_topic("conn1", "cron_topic")

    assert "session_1" in sm._message_subs
    assert "session_2" in sm._message_subs
    assert "cron_topic" in sm._topic_subs

    # Disconnect conn1
    sm.remove_connection("conn1")

    # session_1 had only conn1, so it should be fully cleaned up
    assert "session_1" not in sm._message_subs
    # session_2 still has conn2, so it remains
    assert "session_2" in sm._message_subs
    assert sm.get_message_subscribers("session_2") == {"conn2"}
    # cron_topic had only conn1, so it should be deleted
    assert "cron_topic" not in sm._topic_subs
    # sessions subscription should be removed
    assert "conn1" not in sm.get_session_subscribers()

    # Disconnect conn2
    sm.remove_connection("conn2")
    assert sm._message_subs == {}
    assert sm._topic_subs == {}
    assert sm._session_subs == set()
