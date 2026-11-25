export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败", review: "复核签署",
  admit_volume: "维修库入库", admit_conflict: "入库冲突", withdraw_volume: "撤回维护卷",
});
