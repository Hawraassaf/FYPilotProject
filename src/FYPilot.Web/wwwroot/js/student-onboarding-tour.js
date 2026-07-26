(function () {
    "use strict";

    const shell = document.querySelector("[data-student-onboarding]");
    const overlay = document.getElementById("studentOnboarding");
    const spotlight = document.getElementById("studentOnboardingSpotlight");
    const card = document.getElementById("studentOnboardingCard");
    const counter = document.getElementById("studentOnboardingCounter");
    const progressBar = document.getElementById("studentOnboardingProgressBar");
    const phaseRoute = document.getElementById("studentOnboardingPhaseRoute");
    const icon = document.getElementById("studentOnboardingIcon");
    const kicker = document.getElementById("studentOnboardingKicker");
    const title = document.getElementById("studentOnboardingTitle");
    const description = document.getElementById("studentOnboardingDescription");
    const action = document.getElementById("studentOnboardingAction");
    const closeButton = document.getElementById("studentOnboardingClose");
    const skipButton = document.getElementById("studentOnboardingSkip");
    const backButton = document.getElementById("studentOnboardingBack");
    const nextButton = document.getElementById("studentOnboardingNext");
    const startButton = document.getElementById("studentOnboardingStartButton");
    const restartButton = document.getElementById("startStudentTourButton");

    if (!shell || !overlay || !spotlight || !card) {
        return;
    }

    if (shell.getAttribute("data-onboarding-role") !== "student") {
        return;
    }

    const userId =
        shell.getAttribute("data-onboarding-user-id") || "anonymous";

    const version =
        shell.getAttribute("data-onboarding-version") || "3";

    const currentPath =
        shell.getAttribute("data-onboarding-current-path") || "";

    const storageKey =
        `fypilot.student-guided-journey.v${version}.${userId}`;

    const reduceMotion =
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const coarsePointer =
        window.matchMedia("(pointer: coarse)").matches;

    const phaseOrder = [
        "foundation",
        "intelligence",
        "engineering",
        "support",
        "defense"
    ];

    const phaseThemes = {
        foundation: {
            accent: "#3978ed",
            rgb: "57, 120, 237",
            soft: "#edf4ff"
        },
        intelligence: {
            accent: "#735fe8",
            rgb: "115, 95, 232",
            soft: "#f1eeff"
        },
        engineering: {
            accent: "#168d72",
            rgb: "22, 141, 114",
            soft: "#eaf7f2"
        },
        support: {
            accent: "#cf6b2d",
            rgb: "207, 107, 45",
            soft: "#fff1e8"
        },
        defense: {
            accent: "#d34f65",
            rgb: "211, 79, 101",
            soft: "#fff0f3"
        }
    };

    const steps = [
        {
            type: "welcome",
            phase: "foundation",
            icon: "bi-stars",
            kicker: "Welcome to FYPilot",
            title: "Build your FYP in the right order",
            description:
                "This short tour shows the full path from project creation to defense.",
            action:
                "Start with My Projects, then follow each highlighted section."
        },
        {
            selector: "[data-tour='my-projects']",
            phase: "foundation",
            icon: "bi-folder2-open",
            kicker: "Step 1 — My Projects",
            title: "Create the project workspace",
            description:
                "Your project stores the team, selected idea, roadmap, activity, and feedback.",
            action:
                "Create or join a project, then open its Dashboard."
        },
        {
            selector: "[data-tour='skill-assessment']",
            phase: "foundation",
            icon: "bi-bar-chart-line",
            kicker: "Step 2 — Skill Assessment",
            title: "Tell FYPilot what you can build",
            description:
                "Add your real skills, experience, interests, and preferred technologies.",
            action:
                "Complete this before generating project ideas."
        },
        {
            selector: "[data-tour='idea-generator']",
            phase: "intelligence",
            icon: "bi-lightbulb",
            kicker: "Step 3 — Idea Generator",
            title: "Generate suitable project ideas",
            description:
                "FYPilot creates ideas that match your skills, team, time, and goals.",
            action:
                "Generate several ideas and keep the strongest candidates."
        },
        {
            selector: "[data-tour='idea-comparison']",
            phase: "intelligence",
            icon: "bi-columns-gap",
            kicker: "Step 4 — Idea Comparison",
            title: "Compare before choosing",
            description:
                "See each idea's strengths, weaknesses, scores, and recommendation.",
            action:
                "Choose the idea that best fits your team and time."
        },
        {
            selector: "[data-tour='market-demand']",
            phase: "intelligence",
            icon: "bi-graph-up-arrow",
            kicker: "Step 5 — Market Demand",
            title: "Check whether the problem is needed",
            description:
                "Review real evidence, demand, confidence, and regional opportunity.",
            action:
                "Use strong sources and be honest about low-confidence evidence."
        },
        {
            selector: "[data-tour='project-dna']",
            phase: "intelligence",
            icon: "bi-diagram-3",
            kicker: "Step 6 — Project DNA",
            title: "Understand the project's technical identity",
            description:
                "See the architecture direction, complexity, modules, and differentiators.",
            action:
                "Use this section to explain what makes your project unique."
        },
        {
            selector: "[data-tour='roadmap']",
            phase: "engineering",
            icon: "bi-map",
            kicker: "Step 7 — Roadmap",
            title: "Plan how the project will be built",
            description:
                "Create phases, tasks, durations, hours, dependencies, and team allocation.",
            action:
                "Check that the workload is realistic for every team member."
        },
        {
            selector: "[data-tour='se-documentation']",
            phase: "engineering",
            icon: "bi-journal-code",
            kicker: "Step 8 — SE Documentation",
            title: "Create the engineering report",
            description:
                "Generate requirements, design, database, UI, tests, diagrams, and traceability.",
            action:
                "Review the details and assumptions before downloading."
        },
        {
            selector: "[data-tour='mentor-chat']",
            phase: "support",
            icon: "bi-chat-dots",
            kicker: "Step 9 — Mentor Chat",
            title: "Get help when you are blocked",
            description:
                "Ask project-aware questions about architecture, code, risks, or documentation.",
            action:
                "Ask one focused question at a time."
        },
        {
            selector: "[data-tour='supervisor-feedback']",
            phase: "support",
            icon: "bi-chat-square-text",
            kicker: "Step 10 — Supervisor Feedback",
            title: "Stay aligned with your supervisor",
            description:
                "Keep academic feedback and required changes visible to the whole team.",
            action:
                "Apply important feedback before moving forward."
        },
        {
            selector: "[data-tour='defense-simulator']",
            phase: "defense",
            icon: "bi-shield-check",
            kicker: "Step 11 — Defense Simulator",
            title: "Practice explaining your project",
            description:
                "Answer project-specific questions and receive a score with improvement advice.",
            action:
                "Repeat weak questions until your explanation is clear."
        },
        {
            selector: "[data-tour='dashboard-link']",
            phase: "defense",
            icon: "bi-grid",
            kicker: "Step 12 — Dashboard",
            title: "Track the whole journey here",
            description:
                "See the active project, readiness, next action, activity, and alerts.",
            action:
                "Return often and follow the recommended next step."
        },
        {
            type: "finish",
            phase: "defense",
            icon: "bi-check2-circle",
            kicker: "Tour completed",
            title: "You are ready to start",
            description:
                "Create the project first, then move through each section in order.",
            action:
                "Open My Projects and begin your FYP journey."
        }
    ];

    let currentIndex = 0;
    let currentTarget = null;
    let previousFocus = null;
    let offcanvasOpenedByTour = false;

    function readState() {
        try {
            const raw = localStorage.getItem(storageKey);
            return raw ? JSON.parse(raw) : null;
        } catch {
            return null;
        }
    }

    function saveState(status) {
        try {
            localStorage.setItem(
                storageKey,
                JSON.stringify({
                    status: status,
                    version: version,
                    completedAt: new Date().toISOString()
                })
            );
        } catch {
            // Tour remains usable when storage is unavailable.
        }
    }

    function hasSeenTour() {
        const state = readState();

        return Boolean(
            state
            && (state.status === "completed"
                || state.status === "dismissed")
        );
    }

    function delay(milliseconds) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, milliseconds);
        });
    }

    function setTheme(phase) {
        const theme =
            phaseThemes[phase]
            || phaseThemes.foundation;

        overlay.style.setProperty("--tour-accent", theme.accent);
        overlay.style.setProperty("--tour-accent-rgb", theme.rgb);
        overlay.style.setProperty("--tour-accent-soft", theme.soft);
    }

    function updatePhaseRoute(phase) {
        const activeIndex = phaseOrder.indexOf(phase);

        phaseRoute
            .querySelectorAll("[data-tour-phase]")
            .forEach(function (item) {
                const itemPhase =
                    item.getAttribute("data-tour-phase");

                const itemIndex =
                    phaseOrder.indexOf(itemPhase);

                item.classList.toggle(
                    "is-active",
                    itemPhase === phase);

                item.classList.toggle(
                    "is-complete",
                    itemIndex >= 0
                    && itemIndex < activeIndex);
            });
    }

    function clearTarget() {
        if (currentTarget) {
            currentTarget.classList.remove("student-tour-target");
        }

        currentTarget = null;
    }

    async function ensureMobileSidebar(target) {
        const sidebar = target?.closest("#appSidebar");

        if (!sidebar) {
            return;
        }

        if (!window.matchMedia("(max-width: 991.98px)").matches) {
            return;
        }

        if (!window.bootstrap?.Offcanvas) {
            return;
        }

        const instance =
            window.bootstrap.Offcanvas.getOrCreateInstance(sidebar);

        if (sidebar.classList.contains("show")) {
            return;
        }

        offcanvasOpenedByTour = true;

        await new Promise(function (resolve) {
            const onShown = function () {
                sidebar.removeEventListener(
                    "shown.bs.offcanvas",
                    onShown);

                resolve();
            };

            sidebar.addEventListener(
                "shown.bs.offcanvas",
                onShown);

            instance.show();
        });
    }

    async function revealTarget(target) {
        await ensureMobileSidebar(target);

        const sidebarScroll = target.closest(".sidebar-scroll");

        if (sidebarScroll) {
            const targetCenter =
                target.offsetTop + target.offsetHeight / 2;

            const desiredTop =
                targetCenter - sidebarScroll.clientHeight / 2;

            sidebarScroll.scrollTo({
                top: Math.max(0, desiredTop),
                behavior: reduceMotion ? "auto" : "smooth"
            });
        } else {
            target.scrollIntoView({
                behavior: reduceMotion ? "auto" : "smooth",
                block: "center",
                inline: "nearest"
            });
        }

        await delay(reduceMotion ? 30 : 330);
    }

    function placeCard(rect, target) {
        const gap = 16;
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;
        const cardRect = card.getBoundingClientRect();
        const sidebar = target?.closest(".app-sidebar");

        let placement = "right";
        let left = 14;
        let top = 14;

        if (sidebar && viewportWidth >= 992) {
            const sidebarRect = sidebar.getBoundingClientRect();

            left = sidebarRect.right + gap;
            top =
                rect.top
                + rect.height / 2
                - cardRect.height / 2;

            placement = "right";
        } else {
            const spaceRight = viewportWidth - rect.right;
            const spaceLeft = rect.left;
            const spaceBelow = viewportHeight - rect.bottom;
            const spaceAbove = rect.top;

            if (spaceRight >= cardRect.width + gap) {
                left = rect.right + gap;
                top =
                    rect.top
                    + (rect.height - cardRect.height) / 2;

                placement = "right";
            } else if (spaceLeft >= cardRect.width + gap) {
                left = rect.left - cardRect.width - gap;
                top =
                    rect.top
                    + (rect.height - cardRect.height) / 2;

                placement = "left";
            } else if (spaceBelow >= cardRect.height + gap) {
                left =
                    rect.left
                    + (rect.width - cardRect.width) / 2;

                top = rect.bottom + gap;
                placement = "bottom";
            } else if (spaceAbove >= cardRect.height + gap) {
                left =
                    rect.left
                    + (rect.width - cardRect.width) / 2;

                top = rect.top - cardRect.height - gap;
                placement = "top";
            } else {
                left = (viewportWidth - cardRect.width) / 2;
                top = (viewportHeight - cardRect.height) / 2;
                placement = "center";
            }
        }

        left = Math.min(
            Math.max(12, left),
            viewportWidth - cardRect.width - 12);

        top = Math.min(
            Math.max(12, top),
            viewportHeight - cardRect.height - 12);

        card.style.left = `${left}px`;
        card.style.top = `${top}px`;
        card.dataset.placement = placement;

        if (placement === "right" || placement === "left") {
            const arrowOffset =
                Math.min(
                    Math.max(
                        28,
                        rect.top
                        + rect.height / 2
                        - top
                        - 9),
                    cardRect.height - 36);

            card.style.setProperty(
                "--tour-arrow-offset",
                `${arrowOffset}px`);
        } else if (placement === "top" || placement === "bottom") {
            const arrowOffset =
                Math.min(
                    Math.max(
                        28,
                        rect.left
                        + rect.width / 2
                        - left
                        - 9),
                    cardRect.width - 36);

            card.style.setProperty(
                "--tour-arrow-offset",
                `${arrowOffset}px`);
        }
    }

    function positionSpotlight(target) {
        const targetRect = target.getBoundingClientRect();
        const padding = 8;

        const top = Math.max(6, targetRect.top - padding);
        const left = Math.max(6, targetRect.left - padding);
        const right = Math.min(
            window.innerWidth - 6,
            targetRect.right + padding);

        const bottom = Math.min(
            window.innerHeight - 6,
            targetRect.bottom + padding);

        const rect = {
            top: top,
            left: left,
            right: right,
            bottom: bottom,
            width: Math.max(24, right - left),
            height: Math.max(24, bottom - top)
        };

        spotlight.style.top = `${rect.top}px`;
        spotlight.style.left = `${rect.left}px`;
        spotlight.style.width = `${rect.width}px`;
        spotlight.style.height = `${rect.height}px`;

        const radius =
            window.getComputedStyle(target).borderRadius;

        spotlight.style.borderRadius =
            radius && radius !== "0px"
                ? radius
                : "13px";

        placeCard(rect, target);
    }

    async function renderStep() {
        const step = steps[currentIndex];

        clearTarget();

        overlay.classList.remove(
            "has-target",
            "is-centered",
            "is-finished");

        setTheme(step.phase);
        updatePhaseRoute(step.phase);

        icon.innerHTML = `<i class="bi ${step.icon}"></i>`;
        kicker.textContent = step.kicker;
        title.textContent = step.title;
        description.textContent = step.description;
        action.textContent = step.action;

        const contentSteps = steps.length - 2;

        counter.textContent =
            step.type === "welcome"
                ? "Journey overview"
                : step.type === "finish"
                    ? "Tour complete"
                    : `Step ${currentIndex} of ${contentSteps}`;

        const progress =
            step.type === "welcome"
                ? 3
                : step.type === "finish"
                    ? 100
                    : Math.round(
                        currentIndex
                        / (steps.length - 1)
                        * 100);

        progressBar.style.width = `${progress}%`;
        backButton.disabled = currentIndex === 0;

        nextButton.innerHTML =
            step.type === "welcome"
                ? 'Begin <i class="bi bi-arrow-right"></i>'
                : step.type === "finish"
                    ? 'Finish <i class="bi bi-check2"></i>'
                    : 'Next <i class="bi bi-arrow-right"></i>';

        if (step.type === "welcome" || step.type === "finish") {
            overlay.classList.add("is-centered");

            if (step.type === "finish") {
                overlay.classList.add("is-finished");
            }

            card.style.left = "";
            card.style.top = "";
            card.dataset.placement = "center";
            return;
        }

        const target = document.querySelector(step.selector);

        if (!target) {
            if (currentIndex < steps.length - 1) {
                currentIndex += 1;
                await renderStep();
            }

            return;
        }

        currentTarget = target;

        await revealTarget(target);

        target.classList.add("student-tour-target");
        overlay.classList.add("has-target");
        positionSpotlight(target);
    }

    async function startTour(force) {
        if (!force && hasSeenTour()) {
            return;
        }

        previousFocus = document.activeElement;
        currentIndex = 0;

        overlay.classList.add("is-open");
        overlay.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";

        await renderStep();
        nextButton.focus();
    }

    function hideMobileSidebar() {
        if (!offcanvasOpenedByTour) {
            return;
        }

        const sidebar = document.getElementById("appSidebar");

        if (sidebar && window.bootstrap?.Offcanvas) {
            window.bootstrap.Offcanvas
                .getOrCreateInstance(sidebar)
                .hide();
        }

        offcanvasOpenedByTour = false;
    }

    function closeTour(status) {
        clearTarget();
        saveState(status);
        hideMobileSidebar();

        overlay.classList.remove(
            "is-open",
            "has-target",
            "is-centered",
            "is-finished");

        overlay.setAttribute("aria-hidden", "true");
        document.body.style.overflow = "";

        if (previousFocus instanceof HTMLElement) {
            previousFocus.focus();
        }
    }

    nextButton.addEventListener(
        "click",
        async function () {
            if (currentIndex >= steps.length - 1) {
                closeTour("completed");
                return;
            }

            currentIndex += 1;
            await renderStep();
        });

    backButton.addEventListener(
        "click",
        async function () {
            if (currentIndex <= 0) {
                return;
            }

            currentIndex -= 1;
            await renderStep();
        });

    skipButton.addEventListener(
        "click",
        function () {
            closeTour(
                currentIndex >= steps.length - 1
                    ? "completed"
                    : "dismissed");
        });

    closeButton.addEventListener(
        "click",
        function () {
            closeTour("dismissed");
        });

    startButton.addEventListener(
        "click",
        function () {
            closeTour("completed");

            const myProjects =
                document.querySelector("[data-tour='my-projects']");

            if (myProjects instanceof HTMLAnchorElement) {
                window.location.href = myProjects.href;
            }
        });

    if (restartButton) {
        restartButton.addEventListener(
            "click",
            function () {
                startTour(true);
            });
    }

    window.addEventListener(
        "resize",
        function () {
            if (overlay.classList.contains("is-open") && currentTarget) {
                positionSpotlight(currentTarget);
            }
        });

    document.addEventListener(
        "scroll",
        function () {
            if (overlay.classList.contains("is-open") && currentTarget) {
                positionSpotlight(currentTarget);
            }
        },
        true);

    document.addEventListener(
        "keydown",
        function (event) {
            if (!overlay.classList.contains("is-open")) {
                return;
            }

            if (event.key === "Escape") {
                closeTour("dismissed");
            }

            if (event.key === "ArrowRight") {
                nextButton.click();
            }

            if (event.key === "ArrowLeft" && !backButton.disabled) {
                backButton.click();
            }
        });

    const forceTour =
        new URLSearchParams(window.location.search)
            .get("tour") === "1";

    const normalizedPath = currentPath.toLowerCase();

    const autoStartPage =
        normalizedPath.startsWith("/student/myprojects")
        || normalizedPath.startsWith("/student/dashboard");

    window.setTimeout(
        function () {
            if (forceTour || (autoStartPage && !hasSeenTour())) {
                startTour(true);
            }
        },
        coarsePointer ? 600 : 420);
})();
