"""CLI와 FastAPI가 공유할 실행기. HTTP·업무 DB는 이 모듈 밖에 둔다."""

from threading import Lock
from langgraph.types import Command
from app.backend import ResumeRequest, RunRequest
from app.bridge import review_decision
from app.nodes.review import validate_review


class DecisionError(ValueError):
    """그래프 실행 전에 거부된 결정. 사용자가 다시 입력할 수 있다."""


class NotWaitingError(DecisionError):
    """승인을 기다리는 thread 가 아니다 (없거나 이미 끝났다)."""


class AgentRunner:
    def __init__(self, graph, on_update=None):
        self.graph = graph
        self._locks, self._locks_guard = {}, Lock()
        self.on_update = on_update

    def _lock(self, thread_id):
        """thread 마다 잠근다. 작업 B 의 시작 · 재개가 작업 A 의 LLM 호출이 끝나기를 기다리지 않는다."""
        with self._locks_guard:
            return self._locks.setdefault(thread_id, Lock())

    @staticmethod
    def config(thread_id, **configurable):
        return {"configurable": {"thread_id": thread_id, **configurable}, "recursion_limit": 100}

    def start(
        self,
        task_id,
        regulation_id,
        old_version="v3",
        new_version="v4",
        *,
        thread_id=None,
        article_ids=None,
        listener=None,
    ):
        thread_id = thread_id or task_id
        config = self.config(thread_id)
        # 같은 thread 의 중복 실행/재개를 직렬화한다. 분산 배포에는 외부 잠금이 필요하다.
        with self._lock(thread_id):
            if self.graph.get_state(config).values:
                raise ValueError(
                    "이미 시작한 thread_id입니다. 승인 재개는 resume을 사용하세요"
                )
            return self._invoke(
                {
                    "task_id": task_id,
                    "regulation_id": regulation_id,
                    "old_version": old_version,
                    "new_version": new_version,
                    "article_ids": article_ids or [],
                },
                config,
                listener,
            )

    def resume(self, thread_id, decision, *, task_id=None, step=None, listener=None, job_reader=None):
        """job_reader: 반영(apply) 노드가 큐 작업을 조회할 함수. 요청마다 다를 수 있어 그래프가 아니라 실행 config 로 넘긴다."""
        config = self.config(thread_id, **({"job_reader": job_reader} if job_reader else {}))
        with self._lock(thread_id):
            snapshot = self.graph.get_state(config)
            interrupts = [i for task in snapshot.tasks for i in task.interrupts]
            if not interrupts:
                raise NotWaitingError("승인을 기다리는 작업이 없습니다")
            if task_id and snapshot.values["task_id"] != task_id:
                raise DecisionError("다른 작업의 재개 요청입니다")
            expected = "REVIEW"
            if step and step != expected:
                raise DecisionError("현재는 상품별 제안 검토 단계입니다. 중간 요건 승인은 사용하지 않습니다")
            try:
                validate_review(snapshot.values, decision)
            except ValueError as exc:
                raise DecisionError(str(exc)) from exc
            return self._invoke(Command(resume=decision), config, listener)

    def _invoke(self, value, config, listener=None):
        """listener: node_started(node) · node_updated(node, delta)를 가진 객체. 호출 단위로 받는다."""
        if listener is None and self.on_update is None:
            return self.graph.invoke(value, config)
        if listener is not None:
            if hasattr(listener, "restore"):
                # thread 잠금 안에서 같은 체크포인트를 읽는다. 새 Bridge 와 /continue 모두에 적용한다.
                listener.restore(self.graph.get_state(config).values)
            # 노드가 도구 호출 · 판단을 작업 기록으로 남기도록 호출 단위로 넘긴다 (job_reader와 같은 방식)
            config = {**config, "configurable": {**config["configurable"], "recorder": listener}}
        on_update = listener.node_updated if listener else self.on_update
        interrupts = ()
        for mode, chunk in self.graph.stream(value, config, stream_mode=["updates", "tasks"]):
            if mode == "tasks":
                # 시작 알림에만 input이 있다. 끝 알림은 updates로 받는다.
                if listener and "input" in chunk:
                    listener.node_started(chunk["name"])
                continue
            for node, delta in chunk.items():
                if node == "__interrupt__":
                    interrupts = delta
                else:
                    on_update(node, delta)
        out = dict(self.graph.get_state(config).values)
        if interrupts:
            out["__interrupt__"] = interrupts
        return out

    # ---------- 백엔드 확인 (docs/BACKEND_API.md 10.3) ----------

    def waiting_review(self, thread_id):
        snapshot = self.graph.get_state(self.config(thread_id))
        return any(task.interrupts for task in snapshot.tasks)

    def pending_nodes(self, thread_id):
        """interrupt 없이 멈춘 그래프의 다음 노드. 반영(apply) 도중 프로세스가 죽으면 ('apply',) 다."""
        snapshot = self.graph.get_state(self.config(thread_id))
        if not snapshot.values or any(task.interrupts for task in snapshot.tasks):
            return ()
        return tuple(snapshot.next)

    def continue_pending(self, thread_id, *, task_id=None, listener=None, job_reader=None):
        """멈춘 자리의 다음 노드부터 이어간다. apply 는 같은 멱등키로 다시 부르므로 큐 작업이 중복 생기지 않는다."""
        config = self.config(thread_id, **({"job_reader": job_reader} if job_reader else {}))
        with self._lock(thread_id):
            snapshot = self.graph.get_state(config)
            if not snapshot.values:
                raise NotWaitingError("시작하지 않은 thread_id 입니다")
            if task_id and snapshot.values["task_id"] != task_id:
                raise DecisionError("다른 작업의 재개 요청입니다")
            if any(task.interrupts for task in snapshot.tasks):
                raise DecisionError("승인을 기다리는 중입니다. resume 을 사용하세요")
            if not snapshot.next:
                raise NotWaitingError("이미 끝난 작업입니다")
            return self._invoke(None, config, listener)

    def current_proposals(self, thread_id):
        """검토 대기 중인 제안 (버전 포함). 백엔드 확인 결과를 제안별 결정으로 바꿀 때 쓴다."""
        return list(self.graph.get_state(self.config(thread_id)).values.get("proposals", []))

    def backend_decision(self, request: ResumeRequest):
        """백엔드 확인 요청 → 그래프 검토 명령. 그래프를 돌리기 전에 거부할 수 있다 (400 · 409)."""
        if request.step != "REVIEW":
            raise DecisionError("현재는 상품별 제안 검토 단계입니다. 중간 요건 승인은 사용하지 않습니다")
        if not self.waiting_review(request.thread_id):
            raise NotWaitingError("승인을 기다리는 작업이 없습니다")
        snapshot = self.graph.get_state(self.config(request.thread_id))
        if snapshot.values["task_id"] != request.task_id:
            raise DecisionError("다른 작업의 재개 요청입니다")
        try:
            decision = review_decision(list(snapshot.values.get("proposals", [])), request.decision, request.payload,
                                       request.comment, approval_id=request.approval_id)
            validate_review(snapshot.values, decision)
            return decision
        except ValueError as exc:
            raise DecisionError(str(exc)) from exc

    def resume_backend(self, request: ResumeRequest, *, listener=None, job_reader=None):
        return self.resume(request.thread_id, self.backend_decision(request), task_id=request.task_id,
                           step=request.step, listener=listener, job_reader=job_reader)

    def start_backend(self, request: RunRequest, *, regulation_id, article_ids=None, listener=None):
        return self.start(**request.to_input(regulation_id), article_ids=article_ids, listener=listener)
