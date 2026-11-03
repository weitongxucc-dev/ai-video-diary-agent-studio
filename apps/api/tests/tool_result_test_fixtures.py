"""Expose SDK ToolResult failures in the Node test harness outcome."""

TOOL_OUTCOME_JS = r"""
function toolOutcome(result) {
  return {ok:result.isError !== true,result,
    ...(result.isError === true ? {error:result.content.filter(item=>item.type==='text').map(item=>item.text).join(' ')} : {})};
}
"""
