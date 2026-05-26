/**
 * Takyon Agent Map dashboard plugin.
 *
 * Plain IIFE, no build step. Reads graph/source data from the plugin API.
 */
(function () {
  "use strict";

  const SDK = window.__TAKYON_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;
  const { useEffect, useMemo, useRef, useState } = SDK.hooks;
  const { fetchJSON } = SDK;
  const Input = SDK.components.Input || "input";
  const Badge = SDK.components.Badge || function BadgeShim(props) {
    const { children, className } = props;
    return h("span", { className: "takyon-map-badge " + (className || "") }, children);
  };

  const API = "/api/plugins/takyon-map";
  const LANES = [
    { key: "entry", label: "Entry", x: 20 },
    { key: "shell", label: "Shell", x: 270 },
    { key: "prompts", label: "Prompt Wrappers", x: 520 },
    { key: "ceo", label: "CEO", x: 770 },
    { key: "registry", label: "Registry", x: 1020 },
    { key: "skills", label: "Skills", x: 1270 },
    { key: "tools", label: "Tools", x: 1520 },
    { key: "wakeups", label: "Wakeups", x: 1770 },
  ];
  const LANE_X = Object.fromEntries(LANES.map((lane) => [lane.key, lane.x]));
  const NODE_W = 210;
  const NODE_H = 72;
  const ROW_GAP = 24;

  function cx() {
    return Array.from(arguments).filter(Boolean).join(" ");
  }

  function MapButton(props) {
    const next = Object.assign({}, props, {
      className: cx("takyon-map-button", props.className),
    });
    return h("button", next, props.children);
  }

  function shortPath(path) {
    if (!path) return "";
    const marker = "/hermes-agent-main/";
    const idx = path.indexOf(marker);
    if (idx >= 0) return path.slice(idx + 1);
    const parts = path.split("/");
    return parts.slice(Math.max(0, parts.length - 4)).join("/");
  }

  function nodeTitle(node) {
    return node.label || node.id;
  }

  function sourceLabel(source) {
    if (!source) return "";
    return source.relative_path || shortPath(source.path);
  }

  function useGraph() {
    const [graph, setGraph] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    async function refresh() {
      setLoading(true);
      setError("");
      try {
        const next = await fetchJSON(API + "/graph");
        setGraph(next);
      } catch (err) {
        setError(String(err && err.message ? err.message : err));
      } finally {
        setLoading(false);
      }
    }

    useEffect(function () {
      refresh();
    }, []);

    return { graph, loading, error, refresh };
  }

  function layoutGraph(nodes) {
    const laneCounts = {};
    const byId = {};
    const laid = nodes.map(function (node) {
      const lane = node.lane || "skills";
      const index = laneCounts[lane] || 0;
      laneCounts[lane] = index + 1;
      const wrappedSkills = lane === "skills" && index >= 8;
      const columnOffset = wrappedSkills ? 230 : 0;
      const row = wrappedSkills ? index - 8 : index;
      const positioned = Object.assign({}, node, {
        x: (LANE_X[lane] || 20) + columnOffset,
        y: 58 + row * (NODE_H + ROW_GAP),
      });
      byId[node.id] = positioned;
      return positioned;
    });
    const width = 2030;
    const maxY = laid.reduce(function (value, node) {
      return Math.max(value, node.y + NODE_H + 70);
    }, 720);
    return { nodes: laid, byId, width, height: maxY };
  }

  function GraphView(props) {
    const { graph, selectedId, setSelectedId, query, kindFilter } = props;
    const lower = query.trim().toLowerCase();
    const filteredIds = useMemo(function () {
      const ids = new Set();
      for (const node of graph.nodes) {
        const text = [
          node.id,
          node.label,
          node.kind,
          node.lane,
          node.description,
          (node.tags || []).join(" "),
          shortPath(node.source_path),
        ].join(" ").toLowerCase();
        const matchesQuery = !lower || text.includes(lower);
        const matchesKind = !kindFilter || node.kind === kindFilter || node.lane === kindFilter;
        if (matchesQuery && matchesKind) ids.add(node.id);
      }
      return ids;
    }, [graph.nodes, lower, kindFilter]);

    const layout = useMemo(function () {
      return layoutGraph(graph.nodes);
    }, [graph.nodes]);

    const visibleEdges = graph.edges.filter(function (edge) {
      return filteredIds.has(edge.source) && filteredIds.has(edge.target);
    });

    return h("div", { className: "takyon-map-graph-shell" },
      h("div", { className: "takyon-map-graph", style: { width: layout.width + "px", height: layout.height + "px" } },
        h("svg", {
          className: "takyon-map-edges",
          width: layout.width,
          height: layout.height,
          viewBox: "0 0 " + layout.width + " " + layout.height,
        },
          h("defs", null,
            h("marker", {
              id: "takyon-map-arrow",
              markerWidth: "10",
              markerHeight: "10",
              refX: "9",
              refY: "3",
              orient: "auto",
              markerUnits: "strokeWidth",
            }, h("path", { d: "M0,0 L0,6 L9,3 z", className: "takyon-map-arrow" }))
          ),
          LANES.map(function (lane) {
            return h("g", { key: "lane-" + lane.key },
              h("rect", {
                x: lane.x - 10,
                y: 18,
                width: lane.key === "skills" ? 450 : 230,
                height: layout.height - 38,
                rx: 8,
                className: "takyon-map-lane-bg",
              }),
              h("text", {
                x: lane.x,
                y: 38,
                className: "takyon-map-lane-title",
              }, lane.label)
            );
          }),
          visibleEdges.map(function (edge, index) {
            const source = layout.byId[edge.source];
            const target = layout.byId[edge.target];
            if (!source || !target) return null;
            const x1 = source.x + NODE_W;
            const y1 = source.y + NODE_H / 2;
            const x2 = target.x;
            const y2 = target.y + NODE_H / 2;
            const dx = Math.max(40, (x2 - x1) / 2);
            const d = "M" + x1 + "," + y1 + " C" + (x1 + dx) + "," + y1 + " " + (x2 - dx) + "," + y2 + " " + x2 + "," + y2;
            const midX = (x1 + x2) / 2;
            const midY = (y1 + y2) / 2;
            return h("g", { key: edge.source + edge.target + edge.label + index },
              h("path", {
                d: d,
                className: cx("takyon-map-edge", edge.kind === "reference" && "is-reference"),
                markerEnd: "url(#takyon-map-arrow)",
              }),
              h("text", {
                x: midX,
                y: midY - 6,
                className: "takyon-map-edge-label",
              }, edge.label)
            );
          })
        ),
        layout.nodes.map(function (node) {
          const visible = filteredIds.has(node.id);
          return h("button", {
            key: node.id,
            type: "button",
            className: cx(
              "takyon-map-node",
              "kind-" + node.kind,
              node.id === selectedId && "is-selected",
              !visible && "is-dimmed"
            ),
            style: { left: node.x + "px", top: node.y + "px" },
            onClick: function () { setSelectedId(node.id); },
            title: node.description || node.id,
          },
            h("span", { className: "takyon-map-node-kind" }, node.kind),
            h("span", { className: "takyon-map-node-label" }, nodeTitle(node)),
            h("span", { className: "takyon-map-node-desc" }, node.description || shortPath(node.source_path))
          );
        })
      )
    );
  }

  function Inspector(props) {
    const { graph, selectedNode, setSelectedId, refresh } = props;
    const [source, setSource] = useState(null);
    const [draft, setDraft] = useState("");
    const [loadingSource, setLoadingSource] = useState(false);
    const [sourceError, setSourceError] = useState("");
    const [saving, setSaving] = useState(false);
    const [saveMessage, setSaveMessage] = useState("");
    const textareaRef = useRef(null);
    const inspectorRef = useRef(null);

    const sourcesByPath = useMemo(function () {
      const map = {};
      for (const item of graph.sources || []) map[item.path] = item;
      return map;
    }, [graph.sources]);

    const sourceMeta = selectedNode && selectedNode.source_path ? sourcesByPath[selectedNode.source_path] : null;
    const incoming = selectedNode ? graph.edges.filter(function (edge) { return edge.target === selectedNode.id; }) : [];
    const outgoing = selectedNode ? graph.edges.filter(function (edge) { return edge.source === selectedNode.id; }) : [];
    const nodesById = useMemo(function () {
      const map = {};
      for (const node of graph.nodes) map[node.id] = node;
      return map;
    }, [graph.nodes]);

    const sourceExcerpt = useMemo(function () {
      if (!source || !source.content || !selectedNode) return null;
      const lines = source.content.split(/\r?\n/);
      if (!lines.length) return null;
      const hasFocusedSpan = Boolean(selectedNode.line_start);
      const defaultLineLimit = selectedNode.kind === "skill" || selectedNode.kind === "skill-orphan" ? 220 : 90;
      let start = hasFocusedSpan ? selectedNode.line_start : 1;
      let end = hasFocusedSpan ? (selectedNode.line_end || selectedNode.line_start) : Math.min(lines.length, defaultLineLimit);
      start = Math.max(1, Math.min(start, lines.length));
      end = Math.max(start, Math.min(end, lines.length));
      const rows = [];
      for (let line = start; line <= end; line += 1) {
        rows.push({ number: line, text: lines[line - 1] || "" });
      }
      return {
        start: start,
        end: end,
        total: lines.length,
        rows: rows,
        focused: hasFocusedSpan,
      };
    }, [source && source.path, source && source.content, selectedNode && selectedNode.id]);

    function scrollEditorToLine(lineStart) {
      if (!textareaRef.current || !lineStart) return;
      textareaRef.current.scrollTop = Math.max(0, lineStart - 6) * 18;
    }

    async function loadSource(path, lineStart) {
      if (!path) return;
      setLoadingSource(true);
      setSourceError("");
      setSaveMessage("");
      try {
        const next = await fetchJSON(API + "/source?path=" + encodeURIComponent(path));
        setSource(next);
        setDraft(next.content || "");
        window.setTimeout(function () {
          scrollEditorToLine(lineStart);
        }, 30);
      } catch (err) {
        setSourceError(String(err && err.message ? err.message : err));
      } finally {
        setLoadingSource(false);
      }
    }

    useEffect(function () {
      setSource(null);
      setDraft("");
      setSourceError("");
      setSaveMessage("");
      if (selectedNode && selectedNode.source_path) {
        loadSource(selectedNode.source_path, selectedNode.line_start);
      }
    }, [selectedNode && selectedNode.id]);

    useEffect(function () {
      if (inspectorRef.current) inspectorRef.current.scrollTop = 0;
    }, [selectedNode && selectedNode.id]);

    async function saveSource() {
      if (!source) return;
      setSaving(true);
      setSaveMessage("");
      try {
        const saved = await fetchJSON(API + "/source", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            path: source.path,
            content: draft,
            expected_sha256: source.sha256,
          }),
        });
        setSource(Object.assign({}, source, { sha256: saved.sha256, size: saved.size, content: draft }));
        setSaveMessage("Saved");
        refresh();
      } catch (err) {
        setSaveMessage(String(err && err.message ? err.message : err));
      } finally {
        setSaving(false);
      }
    }

    if (!selectedNode) {
      return h("aside", { className: "takyon-map-inspector", ref: inspectorRef },
        h("div", { className: "takyon-map-empty" },
          h("div", { className: "takyon-map-empty-title" }, "Select a node"),
          h("div", { className: "takyon-map-muted" }, "The graph is generated from the files listed below."),
          h("div", { className: "takyon-map-source-list" },
            (graph.sources || []).slice(0, 80).map(function (item) {
              return h("button", {
                type: "button",
                key: item.path,
                className: "takyon-map-source-row",
                onClick: function () {
                  const node = graph.nodes.find(function (candidate) {
                    return candidate.source_path === item.path;
                  });
                  if (node) setSelectedId(node.id);
                },
              },
                h("span", null, sourceLabel(item)),
                h("span", { className: "takyon-map-source-kind" }, item.kind)
              );
            })
          )
        )
      );
    }

    return h("aside", { className: "takyon-map-inspector", ref: inspectorRef },
      h("div", { className: "takyon-map-inspector-head" },
        h("div", null,
          h("div", { className: "takyon-map-kicker" }, selectedNode.kind),
          h("h2", null, selectedNode.label)
        ),
        h(Badge, { className: "takyon-map-badge" }, selectedNode.lane)
      ),
      selectedNode.description && h("p", { className: "takyon-map-node-summary" }, selectedNode.description),
      h("div", { className: "takyon-map-tags" },
        (selectedNode.tags || []).slice(0, 8).map(function (tag) {
          return h("span", { key: tag, className: "takyon-map-tag" }, tag);
        })
      ),
      sourceMeta && h("div", { className: "takyon-map-source-card" },
        h("div", { className: "takyon-map-source-title" }, sourceLabel(sourceMeta)),
        h("div", { className: "takyon-map-source-meta" },
          sourceMeta.kind,
          sourceMeta.editable ? " editable" : " read-only",
          selectedNode.line_start ? " lines " + selectedNode.line_start + "-" + (selectedNode.line_end || selectedNode.line_start) : ""
        ),
        h("div", { className: "takyon-map-actions" },
          h(MapButton, {
            type: "button",
            size: "xs",
            onClick: function () { loadSource(sourceMeta.path, selectedNode.line_start); },
          }, "Reload")
        )
      ),
      sourceExcerpt && h("section", { className: "takyon-map-snippet-card" },
        h("div", { className: "takyon-map-snippet-head" },
          h("div", { className: "takyon-map-snippet-title" },
            h("strong", null, selectedNode.kind === "skill" || selectedNode.kind === "skill-orphan" ? "Skill prompt" : "Prompt/source excerpt"),
            h("span", null,
              "Lines " + sourceExcerpt.start + "-" + sourceExcerpt.end + " of " + sourceExcerpt.total
            )
          ),
          selectedNode.line_start && h(MapButton, {
            type: "button",
            size: "xs",
            onClick: function () { scrollEditorToLine(selectedNode.line_start); },
          }, "Jump")
        ),
        h("pre", { className: "takyon-map-snippet" },
          sourceExcerpt.rows.map(function (row) {
            return h("div", { key: row.number, className: "takyon-map-snippet-line" },
              h("span", { className: "takyon-map-snippet-number" }, row.number),
              h("code", null, row.text || " ")
            );
          })
        )
      ),
      h("div", { className: "takyon-map-edge-stack" },
        h("div", { className: "takyon-map-edge-column" },
          h("h3", null, "Inputs"),
          incoming.length ? incoming.map(function (edge, index) {
            const node = nodesById[edge.source];
            return h("button", {
              type: "button",
              key: edge.source + index,
              onClick: function () { setSelectedId(edge.source); },
              className: "takyon-map-edge-row",
            }, h("span", null, node ? node.label : edge.source), h("small", null, edge.label));
          }) : h("div", { className: "takyon-map-muted" }, "None")
        ),
        h("div", { className: "takyon-map-edge-column" },
          h("h3", null, "Outputs"),
          outgoing.length ? outgoing.map(function (edge, index) {
            const node = nodesById[edge.target];
            return h("button", {
              type: "button",
              key: edge.target + index,
              onClick: function () { setSelectedId(edge.target); },
              className: "takyon-map-edge-row",
            }, h("span", null, node ? node.label : edge.target), h("small", null, edge.label));
          }) : h("div", { className: "takyon-map-muted" }, "None")
        )
      ),
      h("div", { className: "takyon-map-editor-wrap" },
        h("div", { className: "takyon-map-editor-toolbar" },
          h("span", null, source ? source.relative_path : (loadingSource ? "Loading source" : "No source")),
          source && source.editable && h(MapButton, {
            type: "button",
            size: "xs",
            disabled: saving || draft === source.content,
            onClick: saveSource,
          }, saving ? "Saving" : "Save"),
          source && !source.editable && h("span", { className: "takyon-map-muted" }, "Read-only"),
          saveMessage && h("span", { className: cx("takyon-map-save-message", saveMessage === "Saved" && "is-ok") }, saveMessage)
        ),
        sourceError && h("div", { className: "takyon-map-error" }, sourceError),
        source && h("textarea", {
          ref: textareaRef,
          className: "takyon-map-editor",
          value: draft,
          spellCheck: false,
          readOnly: !source.editable,
          onChange: function (event) { setDraft(event.target.value); },
        })
      ),
      selectedNode.metadata && h("details", { className: "takyon-map-metadata" },
        h("summary", null, "Metadata"),
        h("pre", null, JSON.stringify(selectedNode.metadata, null, 2))
      )
    );
  }

  function Sidebar(props) {
    const { graph, selectedId, setSelectedId, query, setQuery, kindFilter, setKindFilter } = props;
    const kinds = useMemo(function () {
      const seen = new Set();
      for (const node of graph.nodes) {
        seen.add(node.kind);
        seen.add(node.lane);
      }
      return Array.from(seen).sort();
    }, [graph.nodes]);
    const lower = query.trim().toLowerCase();
    const nodes = graph.nodes.filter(function (node) {
      const text = [node.label, node.kind, node.lane, node.description, shortPath(node.source_path)].join(" ").toLowerCase();
      const queryMatch = !lower || text.includes(lower);
      const kindMatch = !kindFilter || node.kind === kindFilter || node.lane === kindFilter;
      return queryMatch && kindMatch;
    });

    return h("nav", { className: "takyon-map-sidebar" },
      h("div", { className: "takyon-map-search" },
        h(Input, {
          value: query,
          onChange: function (event) { setQuery(event.target.value); },
          placeholder: "Search graph",
          className: "takyon-map-input",
        }),
        h("select", {
          value: kindFilter,
          onChange: function (event) { setKindFilter(event.target.value); },
          className: "takyon-map-select",
        },
          h("option", { value: "" }, "All"),
          kinds.map(function (kind) {
            return h("option", { key: kind, value: kind }, kind);
          })
        )
      ),
      h("div", { className: "takyon-map-summary-grid" },
        h("div", null, h("strong", null, graph.summary.nodes), h("span", null, "Nodes")),
        h("div", null, h("strong", null, graph.summary.edges), h("span", null, "Edges")),
        h("div", null, h("strong", null, graph.summary.skills_registered), h("span", null, "Skills")),
        h("div", null, h("strong", null, graph.summary.tools), h("span", null, "Tools"))
      ),
      graph.warnings && graph.warnings.length > 0 && h("div", { className: "takyon-map-warnings" },
        graph.warnings.slice(0, 5).map(function (warning, index) {
          return h("div", { key: index }, warning);
        })
      ),
      h("div", { className: "takyon-map-node-list" },
        nodes.map(function (node) {
          return h("button", {
            key: node.id,
            type: "button",
            className: cx("takyon-map-list-row", node.id === selectedId && "is-active"),
            onClick: function () { setSelectedId(node.id); },
          },
            h("span", null, node.label),
            h("small", null, node.kind)
          );
        })
      )
    );
  }

  function AgentMapApp() {
    const { graph, loading, error, refresh } = useGraph();
    const [selectedId, setSelectedId] = useState("");
    const [query, setQuery] = useState("");
    const [kindFilter, setKindFilter] = useState("");

    useEffect(function () {
      if (graph && !selectedId) setSelectedId("skill:takyon:ceo");
    }, [graph, selectedId]);

    const selectedNode = graph ? graph.nodes.find(function (node) { return node.id === selectedId; }) || null : null;

    if (loading) {
      return h("div", { className: "takyon-map takyon-map-loading" }, "Loading agent map");
    }
    if (error) {
      return h("div", { className: "takyon-map takyon-map-error-page" },
        h("h2", null, "Agent Map failed"),
        h("pre", null, error),
        h(MapButton, { type: "button", onClick: refresh }, "Retry")
      );
    }
    if (!graph) return null;

    return h("div", { className: "takyon-map" },
      h("header", { className: "takyon-map-header" },
        h("div", null,
          h("div", { className: "takyon-map-kicker" }, "Live Takyon source graph"),
          h("h1", null, "Agent Map")
        ),
        h("div", { className: "takyon-map-header-meta" },
          h("span", null, shortPath(graph.generated_from.registry)),
          h(MapButton, { type: "button", size: "xs", onClick: refresh }, "Refresh")
        )
      ),
      h("main", { className: "takyon-map-layout" },
        h(Sidebar, {
          graph: graph,
          selectedId: selectedId,
          setSelectedId: setSelectedId,
          query: query,
          setQuery: setQuery,
          kindFilter: kindFilter,
          setKindFilter: setKindFilter,
        }),
        h(GraphView, {
          graph: graph,
          selectedId: selectedId,
          setSelectedId: setSelectedId,
          query: query,
          kindFilter: kindFilter,
        }),
        h(Inspector, {
          graph: graph,
          selectedNode: selectedNode,
          setSelectedId: setSelectedId,
          refresh: refresh,
        })
      )
    );
  }

  window.__TAKYON_PLUGINS__.register("takyon-map", AgentMapApp);
})();
