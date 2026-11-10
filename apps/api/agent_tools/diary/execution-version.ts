import type {TaskVersion} from './contracts.ts';

/** Follow only a render submitted by this runtime, with the server's adoption proof. */
export function createExecutionVersionFollower(taskId: string, selected: TaskVersion | null | undefined) {
  let submitted: {jobId: string; base: TaskVersion} | null = null;
  const same = (a: TaskVersion, b: TaskVersion) => a.timeline_version_id === b.timeline_version_id
    && a.timeline_sequence === b.timeline_sequence;
  function observe(status: any) {
    const proof = status.output_version;
    if (selected && submitted && status.task_id === taskId && status.job?.task_id === taskId
        && status.job.id === submitted.jobId && status.job.status === 'succeeded' && status.video_ready === true
        && proof?.job_id === submitted.jobId && proof.base_version && proof.result_version
        && same(proof.base_version, submitted.base) && same(selected, submitted.base)
        && typeof proof.result_version.timeline_version_id === 'string'
        && Number.isInteger(proof.result_version.timeline_sequence)
        && proof.result_version.timeline_sequence === submitted.base.timeline_sequence + 1) {
      Object.assign(selected, proof.result_version);
    }
  }
  return {observe, submitted(status: any) {
    if (selected && status.task_id === taskId && status.job?.task_id === taskId && typeof status.job.id === 'string') {
      submitted = {jobId: status.job.id, base: {...selected}};
      observe(status);
    }
  }};
}
