/*
    Centralized AI Agent Loading System -- shared client.

    The browser is a pure observer: every stage circle, message, and
    counter here is rendered only from what /api/ai-agent-jobs/* reports as
    already having happened server-side (AiAgentJob rows, driven by
    AiAgentJobCoordinator independently of this page even existing). This
    module never fabricates progress, never finalizes a job, and never
    calls a "finalize" endpoint -- completion is entirely server-owned;
    once a job_completed event/status is observed, a reload-style page just
    navigates to show the already-persisted result, and an inline-result
    page fetches the safe result view via GET .../result.

    Public API:
        AiAgentProgress.start(options)             -- POST startUrl, then watch
        AiAgentProgress.attach(jobId, options)      -- resume watching a known job
        AiAgentProgress.reconnectViaCurrent(options) -- client-driven fallback lookup

    options:
        agentName, storageKey, stages: [{key,label}, ...],
        startUrl, startBody,
        renderTarget (optional DOM node -> compact inline mode),
        onInlineResult (optional function(resultDto))
*/
(function () {
    "use strict";

    var POLL_FALLBACK_MS = 1500;

    function getAntiforgeryToken() {
        var el = document.querySelector('[name="__RequestVerificationToken"]');
        return el ? el.value : null;
    }

    function escapeHtml(value) {
        var div = document.createElement("div");
        div.textContent = value == null ? "" : String(value);
        return div.innerHTML;
    }

    function buildStageMarkup(stages) {
        return (stages || []).map(function (stage) {
            return (
                '<li class="aip-stage" data-stage-key="' + escapeHtml(stage.key) + '">' +
                '<span class="aip-stage-circle" aria-hidden="true"></span>' +
                '<span class="aip-stage-label">' + escapeHtml(stage.label) + "</span>" +
                "</li>"
            );
        }).join("");
    }

    function stageIcon(state) {
        if (state === "completed") return '<i class="bi bi-check-lg"></i>';
        if (state === "failed") return '<i class="bi bi-x-lg"></i>';
        if (state === "skipped" || state === "cancelled") return '<i class="bi bi-dash-lg"></i>';
        return "";
    }

    function AgentProgressController(options) {
        options = options || {};
        this.agentName = options.agentName || "";
        this.stages = options.stages || [];
        this.storageKey = options.storageKey || "aip:" + this.agentName;
        this.renderTarget = options.renderTarget || null;
        this.onInlineResult = options.onInlineResult || null;

        this.jobId = null;
        this.lastAppliedSequence = -1;
        this.eventSource = null;
        this.pollTimer = null;
        this.finalizing = false;
        this.startedAt = null;
        this.elapsedTimer = null;

        this._buildDom();
    }

    AgentProgressController.prototype._buildDom = function () {
        if (this.renderTarget) {
            this.renderTarget.classList.add("aip-inline");
            this.renderTarget.innerHTML =
                '<div class="aip-inline-message"></div><ol class="aip-stages"></ol>';
            this.stagesEl = this.renderTarget.querySelector(".aip-stages");
            this.messageEl = this.renderTarget.querySelector(".aip-inline-message");
        } else {
            this.overlayEl = document.getElementById("aiAgentProgressOverlay");
            this.stagesEl = document.getElementById("aipStages");
            this.messageEl = document.getElementById("aipMessage");
            this.agentNameEl = document.getElementById("aipAgentName");
            this.providerModelEl = document.getElementById("aipProviderModel");
            this.elapsedEl = document.getElementById("aipElapsed");
            this.cancelBtn = document.getElementById("aipCancelButton");
            this.cancelBanner = document.getElementById("aipCancelBanner");
            this.failedPanel = document.getElementById("aipFailedPanel");
            this.failedMessageEl = document.getElementById("aipFailedMessage");
            this.completedPanel = document.getElementById("aipCompletedPanel");

            if (this.agentNameEl) {
                this.agentNameEl.textContent = this.agentName;
            }
            if (this.cancelBtn) {
                var self = this;
                this.cancelBtn.onclick = function () {
                    self.cancel();
                };
            }
        }

        if (this.stagesEl) {
            this.stagesEl.innerHTML = buildStageMarkup(this.stages);
        }
    };

    AgentProgressController.prototype._show = function () {
        if (this.overlayEl) {
            this.overlayEl.classList.add("is-visible");
            this.overlayEl.setAttribute("aria-hidden", "false");
        }
    };

    AgentProgressController.prototype._hide = function () {
        if (this.overlayEl) {
            this.overlayEl.classList.remove("is-visible");
            this.overlayEl.setAttribute("aria-hidden", "true");
        }
    };

    AgentProgressController.prototype._renderStageStates = function (stageStates) {
        if (!this.stagesEl || !stageStates) {
            return;
        }

        var items = this.stagesEl.querySelectorAll(".aip-stage");
        items.forEach(function (li) {
            var key = li.getAttribute("data-stage-key");
            var state = stageStates[key] || "upcoming";
            li.classList.remove("is-current", "is-completed", "is-failed", "is-skipped", "is-cancelled");
            if (state !== "upcoming") {
                li.classList.add("is-" + state);
            }
            var circle = li.querySelector(".aip-stage-circle");
            if (circle) {
                circle.innerHTML = stageIcon(state);
            }
        });

        // Presentation only -- never mutates stage state, just keeps the
        // active circle visible in the horizontal scroller.
        var current = this.stagesEl.querySelector(".is-current");
        if (current && current.scrollIntoView) {
            current.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
        }
    };

    AgentProgressController.prototype._renderLive = function (data) {
        if (this.messageEl) {
            this.messageEl.textContent = data.message || "";
        }

        if (this.providerModelEl) {
            var parts = [];
            if (data.provider) parts.push(data.provider);
            if (data.model) parts.push(data.model);
            var text = parts.join(" · ");

            if (data.currentAttemptChunkCount) {
                text += (text ? " · " : "") + data.currentAttemptChunkCount + " response chunks received";
                if (data.currentAttemptTokenCount) {
                    text += ", " + data.currentAttemptTokenCount + " tokens";
                }
            }

            this.providerModelEl.textContent = text;
        }
    };

    AgentProgressController.prototype._startElapsedTimer = function () {
        this.startedAt = Date.now();
        if (!this.elapsedEl) {
            return;
        }
        var self = this;
        this.elapsedTimer = window.setInterval(function () {
            var seconds = Math.floor((Date.now() - self.startedAt) / 1000);
            self.elapsedEl.textContent = seconds + "s elapsed";
        }, 1000);
    };

    AgentProgressController.prototype._stopElapsedTimer = function () {
        if (this.elapsedTimer) {
            window.clearInterval(this.elapsedTimer);
            this.elapsedTimer = null;
        }
    };

    // ── Lifecycle ──────────────────────────────────────────────────────────

    AgentProgressController.prototype.start = function (startUrl, startBody) {
        var self = this;
        var headers = { "Content-Type": "application/json" };
        var token = getAntiforgeryToken();
        if (token) {
            headers["RequestVerificationToken"] = token;
        }

        return fetch(startUrl, {
            method: "POST",
            headers: headers,
            body: JSON.stringify(startBody || {}),
        })
            .then(function (resp) {
                if (!resp.ok) {
                    return resp
                        .json()
                        .catch(function () {
                            return {};
                        })
                        .then(function (body) {
                            throw new Error(body.message || "Failed to start (" + resp.status + ")");
                        });
                }
                return resp.json();
            })
            .then(function (data) {
                self.attach(data.jobId);
            });
    };

    AgentProgressController.prototype.attach = function (jobId) {
        this.jobId = jobId;
        try {
            sessionStorage.setItem(this.storageKey, JSON.stringify({ jobId: jobId }));
        } catch (e) {
            // sessionStorage unavailable (private browsing etc.) -- fine,
            // it is only ever a fast-path hint, never required.
        }
        this._show();
        this._startElapsedTimer();
        this._watch();
    };

    AgentProgressController.prototype._watch = function () {
        var self = this;
        fetch("/api/ai-agent-jobs/" + this.jobId)
            .then(function (r) {
                return r.ok ? r.json() : null;
            })
            .then(function (snapshot) {
                if (!snapshot) {
                    return;
                }

                self.lastAppliedSequence = snapshot.lastEventSequence || -1;
                self._renderStageStates(snapshot.stageStates || {});
                self._renderLive(snapshot);

                if (snapshot.status === "completed") {
                    self._onTerminal("job_completed", snapshot);
                    return;
                }
                if (snapshot.status === "failed") {
                    self._onTerminal("job_failed", snapshot);
                    return;
                }
                if (snapshot.status === "cancelled") {
                    self._onTerminal("job_cancelled", snapshot);
                    return;
                }

                self._openEventSource();
            });
    };

    AgentProgressController.prototype._openEventSource = function () {
        var self = this;
        if (this.eventSource) {
            this.eventSource.close();
        }

        var es = new EventSource("/api/ai-agent-jobs/" + this.jobId + "/events");
        this.eventSource = es;

        es.onmessage = function (evt) {
            var data;
            try {
                data = JSON.parse(evt.data);
            } catch (e) {
                return;
            }
            self._handleEvent(data);
        };

        es.onerror = function () {
            es.close();
            self.eventSource = null;
            self._startPollFallback();
        };
    };

    AgentProgressController.prototype._startPollFallback = function () {
        if (this.pollTimer) {
            return;
        }
        var self = this;
        this.pollTimer = window.setInterval(function () {
            fetch("/api/ai-agent-jobs/" + self.jobId)
                .then(function (r) {
                    return r.ok ? r.json() : null;
                })
                .then(function (snapshot) {
                    if (!snapshot) {
                        return;
                    }

                    var terminalType =
                        snapshot.status === "completed" ? "job_completed" :
                        snapshot.status === "failed" ? "job_failed" :
                        snapshot.status === "cancelled" ? "job_cancelled" : "progress";

                    self._handleEvent({
                        sequence: snapshot.lastEventSequence,
                        type: terminalType,
                        status: snapshot.status,
                        stageKey: snapshot.stageKey,
                        stageStates: snapshot.stageStates,
                        message: snapshot.message,
                        provider: snapshot.provider,
                        model: snapshot.model,
                        currentAttemptChunkCount: snapshot.currentAttemptChunkCount,
                        currentAttemptTokenCount: snapshot.currentAttemptTokenCount,
                        errorCode: snapshot.errorCode,
                        errorMessage: snapshot.errorMessage,
                    });

                    if (!self.eventSource && terminalType === "progress") {
                        self._tryReconnectSse();
                    }
                });
        }, POLL_FALLBACK_MS);
    };

    AgentProgressController.prototype._tryReconnectSse = function () {
        var self = this;
        var es = new EventSource("/api/ai-agent-jobs/" + this.jobId + "/events");

        es.onopen = function () {
            self._stopPollFallback();
            self.eventSource = es;
            es.onmessage = function (evt) {
                var data;
                try {
                    data = JSON.parse(evt.data);
                } catch (e) {
                    return;
                }
                self._handleEvent(data);
            };
            es.onerror = function () {
                es.close();
                self.eventSource = null;
                self._startPollFallback();
            };
        };

        es.onerror = function () {
            es.close(); // still down -- the next poll tick tries again
        };
    };

    AgentProgressController.prototype._stopPollFallback = function () {
        if (this.pollTimer) {
            window.clearInterval(this.pollTimer);
            this.pollTimer = null;
        }
    };

    AgentProgressController.prototype._handleEvent = function (evt) {
        // The core anti-regression guard -- applies uniformly to both SSE
        // and the polling fallback, so neither path can ever visually roll
        // a stage backward.
        if (typeof evt.sequence === "number") {
            if (evt.sequence <= this.lastAppliedSequence) {
                return;
            }
            this.lastAppliedSequence = evt.sequence;
        }

        if (evt.stageStates) {
            this._renderStageStates(evt.stageStates);
        }
        this._renderLive(evt);

        if (evt.status === "cancel_requested" && this.cancelBanner) {
            this.cancelBanner.hidden = false;
        }

        if (evt.type === "job_completed" || evt.type === "job_failed" || evt.type === "job_cancelled") {
            this._onTerminal(evt.type, evt);
        }
    };

    AgentProgressController.prototype._onTerminal = function (type, data) {
        this._stopElapsedTimer();
        this._stopPollFallback();
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }

        if (type === "job_completed") {
            this._finalizeOnce();
            return;
        }

        try {
            sessionStorage.removeItem(this.storageKey);
        } catch (e) {
            // ignore
        }

        if (type === "job_failed") {
            if (this.failedPanel) {
                this.failedPanel.hidden = false;
                if (this.failedMessageEl) {
                    this.failedMessageEl.textContent =
                        data.errorMessage || "The AI service could not complete this request.";
                }
            }
        } else if (type === "job_cancelled") {
            if (this.cancelBanner) {
                this.cancelBanner.hidden = true;
            }
        }
    };

    AgentProgressController.prototype._finalizeOnce = function () {
        // Never finalizes anything server-side -- by the time job_completed
        // is observed, AiAgentJobCoordinator has already persisted the
        // result independently of this browser. This only decides how to
        // SHOW that already-saved result.
        if (this.finalizing) {
            return;
        }
        this.finalizing = true;

        try {
            sessionStorage.removeItem(this.storageKey);
        } catch (e) {
            // ignore
        }

        var self = this;

        if (this.onInlineResult) {
            fetch("/api/ai-agent-jobs/" + this.jobId + "/result")
                .then(function (r) {
                    return r.ok ? r.json() : null;
                })
                .then(function (result) {
                    self.onInlineResult(result);
                    self._hide();
                });
            return;
        }

        if (this.completedPanel) {
            this.completedPanel.hidden = false;
        }

        window.setTimeout(function () {
            window.location.href = window.location.pathname + window.location.search;
        }, 500);
    };

    AgentProgressController.prototype.cancel = function () {
        if (!this.jobId) {
            return;
        }
        var headers = {};
        var token = getAntiforgeryToken();
        if (token) {
            headers["RequestVerificationToken"] = token;
        }
        fetch("/api/ai-agent-jobs/" + this.jobId + "/cancel", { method: "POST", headers: headers });
        if (this.cancelBtn) {
            this.cancelBtn.disabled = true;
        }
    };

    // ── Public API ─────────────────────────────────────────────────────────

    window.AiAgentProgress = {
        start: function (options) {
            var controller = new AgentProgressController(options);
            return controller.start(options.startUrl, options.startBody).then(function () {
                return controller;
            });
        },

        // Resume watching a job whose id is already known -- used for the
        // server-rendered active-job hint (data-active-job-id, the primary
        // reconnect path) and for the sessionStorage fast-path.
        attach: function (jobId, options) {
            var controller = new AgentProgressController(options);
            controller.attach(jobId);
            return controller;
        },

        // Pure client-driven fallback lookup (GET /api/ai-agent-jobs/current)
        // for pages/situations without a server-rendered hint. Never falls
        // back to an unrelated completed job -- see AiAgentJobService.FindJobByHashAsync.
        reconnectViaCurrent: function (options) {
            var params = new URLSearchParams({ agentName: options.agentName });
            if (options.projectId != null) {
                params.set("projectId", options.projectId);
            }
            if (options.requestHash) {
                params.set("requestHash", options.requestHash);
            }

            return fetch("/api/ai-agent-jobs/current?" + params.toString()).then(function (r) {
                if (r.status === 204) {
                    return null;
                }
                return r.json().then(function (snapshot) {
                    var controller = new AgentProgressController(options);
                    controller.attach(snapshot.jobId);
                    return controller;
                });
            });
        },
    };
})();
