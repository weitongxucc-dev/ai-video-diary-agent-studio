"""Own a real assistant control for one explicit test scope."""
import time


def isolate_assistant_turn_control(monkeypatch, request):
    import app.assistant_pi as pi
    from app.assistant.turn_control import TurnControl

    control = TurnControl(terminate=lambda process, **kwargs: pi._terminate_process_group(process, **kwargs))
    monkeypatch.setattr(pi, "_turn_control", control)
    monkeypatch.setattr(pi, "_turn_stop_events", control.stop_events)
    # This finalizer precedes monkeypatch restoration;release errors fail the test.
    request.addfinalizer(lambda: control.stop_all(deadline_monotonic=time.monotonic() + 5))
    return control
