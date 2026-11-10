/** Use Pi's native compaction before a long-window model accumulates costly history. */
export function configureContextCompaction(settingsManager, model) {
  const existing = settingsManager.getCompactionSettings(model);
  settingsManager.applyOverrides({compaction:{
    enabled:true,
    reserveTokens:Math.max(existing.reserveTokens, model.contextWindow-128000),
    keepRecentTokens:existing.keepRecentTokens,
  }});
}
