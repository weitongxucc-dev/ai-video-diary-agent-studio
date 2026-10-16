"""Derive goal authorization from existing consumed input receipts, without a queue."""
import json
from app.assistant.inputs import active_input


def input_goal_authorization(conn, root, turn):
    current=active_input(json.loads(turn["events_json"]))
    if current is None:
        return None
    chain=[]
    rows=conn.execute("SELECT id,events_json FROM assistant_conversation_turns WHERE conversation_id=? "
        "AND sequence>=? AND sequence<=? ORDER BY sequence",(root["conversation_id"],root["sequence"],turn["sequence"])).fetchall()
    for row in rows:
        for item in json.loads(row["events_json"]):
            if item.get("type") == "assistant_input" and item["status"] in {"consumed","completed"}:
                chain.append({**item,"source_turn_id":row["id"]})
    position=next(index for index,item in enumerate(chain) if item["input_id"] == current["input_id"])
    chain=chain[:position+1]
    follow_up=next((index for index in range(len(chain)-1,-1,-1) if chain[index]["mode"] == "follow_up"),None)
    selected=chain[follow_up:] if follow_up is not None else chain
    instructions=([root["user_message"]] if follow_up is None else [])+[item["message"] for item in selected]
    instruction="\n\n后续用户要求（与前文冲突时以后文为准）：\n".join(instructions)
    context={key:value for key,value in current["request_context"].items()
        if key not in {"source_kind","original_turn_id","source_turn_id","business_event_id","goal_revision","goal_instruction","source_input_id","source_input_turn_id"}}
    return {"source_input_id":current["input_id"],"source_input_turn_id":turn["id"],
        "original_instruction":instruction,"request_context":context,
        "authorization_inputs":[{"turn_id":item["source_turn_id"],"input_id":item["input_id"]} for item in selected],
        "includes_root_instruction":follow_up is None,"input_sequence":position+1}
