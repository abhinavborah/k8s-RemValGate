# k8s-RemValGate

A small fine-tuned validity gate for agentic Kubernetes incident remediation. An LLM planner samples
several candidate fixes for an incident, the gate scores whether each one will restore the service,
and the system applies the best one only if it clears a calibrated threshold, otherwise it escalates
to a human. The system is evaluated on AIOpsLab mitigation problems against seven baselines.

This is a group project for the course Language Models and Agents (CL3410).

## Status

Planning. The requirements, epics and acceptance criteria are in [docs/brd.md](docs/brd.md), and the
work is tracked as issues on this repository's GitHub Project. There is no runnable code yet.

## Setup

Install and test commands will be added by the repository layout story, together with the pinned
AIOpsLab commit and the model hosting notes.

## Large files

Model weights, rollout logs and datasets are not committed. They live on shared drive storage and
are referenced by checksum.
