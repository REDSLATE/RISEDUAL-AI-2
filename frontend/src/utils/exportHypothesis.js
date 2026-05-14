/**
 * Export an AI Hypothesis report as a text file download.
 */
export function exportHypothesisReport(hypothesis, symbol) {
  const lines = [
    `RISEDUAL AI - HYPOTHESIS REPORT`, `${'='.repeat(50)}`,
    `Symbol: ${hypothesis.symbol}`, `Model: ${hypothesis.model || 'Alpha 1.6'}`,
    `Generated: ${new Date().toLocaleString()}`, '',
    `VERDICT: ${hypothesis.verdict}`, `Confidence: ${hypothesis.confidence}%`,
    hypothesis.agreement != null ? `Model Agreement: ${hypothesis.agreement}%` : '', '',
    `SUMMARY`, `${'─'.repeat(10)}`, hypothesis.summary || 'N/A', '',
  ];
  if (hypothesis.individual_results?.length) {
    lines.push('INDIVIDUAL MODEL RESULTS', '─'.repeat(25));
    hypothesis.individual_results.forEach(r => lines.push(`${r.model}: ${r.verdict} (${r.confidence}% confidence)`));
    lines.push('');
  }
  if (hypothesis.catalysts?.length) {
    lines.push('CATALYSTS', '─'.repeat(10));
    hypothesis.catalysts.forEach((c, i) => lines.push(`${i + 1}. ${c}`));
    lines.push('');
  }
  if (hypothesis.risks?.length) {
    lines.push('RISKS', '─'.repeat(6));
    hypothesis.risks.forEach((r, i) => lines.push(`${i + 1}. ${r}`));
    lines.push('');
  }
  lines.push('', '(c) RISEDUAL AI - risedual.ai');
  const blob = new Blob([lines.filter(Boolean).join('\n')], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `RISEDUAL_AI_${hypothesis.symbol}_${hypothesis.model || 'Report'}.txt`;
  a.click();
  URL.revokeObjectURL(url);
}
