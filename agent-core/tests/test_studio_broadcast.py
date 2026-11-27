import pytest
from sarae_agent.studio.broadcast import BroadcastWatch, read_room

@pytest.mark.parametrize("raw,state", [(2,"live"),(4,"offline"),(0,"unknown"),(1,"unknown"),(None,"unknown")])
def test_only_explicit_live_status_is_live(raw,state):
    result=read_room({"data":{"user":{"roomId":"123"},"liveRoom":{"status":raw,"title":"Title","liveRoomStats":{"userCount":42}}}},"shop")
    assert result["state"]==state
    assert result["viewers"]==(42 if state=="live" else None)
    assert result["room_id"]==("123" if state=="live" else None)

def test_account_change_discards_an_in_flight_read():
    watch=None
    def check(name):
        watch.follow("new.shop")
        return {"state":"live","username":name,"checked_at":1000}
    watch=BroadcastWatch(check,clock=lambda:1000)
    watch.follow("old.shop")
    watch.check_once()
    assert watch.view()["username"]=="new.shop" and watch.view()["state"]=="checking"

def test_stale_or_failed_read_never_reports_live():
    now=[1000]
    watch=BroadcastWatch(lambda name:{"state":"live","username":name,"checked_at":1000},clock=lambda:now[0])
    watch.follow("shop")
    watch.check_once()
    assert watch.view()["state"]=="live"
    now[0]=1100
    assert watch.view()["state"]=="unknown"
    def failed(name): raise TimeoutError()
    watch.checker=failed
    watch.check_once()
    assert watch.view()["state"]=="unknown" and watch.view()["error"]
