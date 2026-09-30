import { useEffect, useRef, useState } from "react";

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

export default function DrawingViewer({
  drawingId,
  page,
  highlight,
  extractedText,
  expanded,
  onOpen,
  onClose,
}) {
  return (
    <div className="doc">
      <p className="eyebrow">
        Drawing {drawingId} · page {page}
        {highlight ? " · cited region" : ""}
      </p>
      <button type="button" className="sheet-open" onClick={onOpen}>
        <div className="sheet preview">
          <img
            alt={`${drawingId} page ${page}`}
            src={`/api/drawings/${drawingId}/page/${page}`}
          />
          {highlight && (
            <div
              className="crop"
              style={{
                left: `${highlight.x * 100}%`,
                top: `${highlight.y * 100}%`,
                width: `${highlight.w * 100}%`,
                height: `${highlight.h * 100}%`,
              }}
            />
          )}
        </div>
        <span className="sheet-hint">Open drawing · zoom and pan</span>
      </button>
      {extractedText && <p className="extract">Extracted text: {extractedText}</p>}
      {expanded && (
        <Lightbox
          drawingId={drawingId}
          page={page}
          highlight={highlight}
          onClose={onClose}
        />
      )}
    </div>
  );
}

function Lightbox({ drawingId, page, highlight, onClose }) {
  const stage = useRef(null);
  const image = useRef(null);
  const drag = useRef(null);
  const view = useRef({ scale: 1, x: 0, y: 0 });
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });

  function apply(nextScale, nextPan) {
    view.current = { scale: nextScale, x: nextPan.x, y: nextPan.y };
    setScale(nextScale);
    setPan(nextPan);
  }

  function place() {
    const host = stage.current;
    const img = image.current;
    if (!host || !img || !img.naturalWidth) {
      return;
    }
    const fit = Math.min(host.clientWidth / img.naturalWidth, host.clientHeight / img.naturalHeight);
    let nextScale = fit * 0.92;
    let hx = img.naturalWidth / 2;
    let hy = img.naturalHeight / 2;
    if (highlight) {
      nextScale = clamp(fit * 2.3, fit * 1.15, 6);
      hx = (highlight.x + highlight.w / 2) * img.naturalWidth;
      hy = (highlight.y + highlight.h / 2) * img.naturalHeight;
    }
    apply(nextScale, {
      x: host.clientWidth / 2 - hx * nextScale,
      y: host.clientHeight / 2 - hy * nextScale,
    });
  }

  function zoomBy(factor, origin) {
    const host = stage.current;
    if (!host) {
      return;
    }
    const current = view.current;
    const rect = host.getBoundingClientRect();
    const mx = origin ? origin.x - rect.left : host.clientWidth / 2;
    const my = origin ? origin.y - rect.top : host.clientHeight / 2;
    const nextScale = clamp(current.scale * factor, 0.2, 12);
    apply(nextScale, {
      x: mx - ((mx - current.x) * nextScale) / current.scale,
      y: my - ((my - current.y) * nextScale) / current.scale,
    });
  }

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (image.current?.naturalWidth) {
      place();
    }
  }, [drawingId, page, highlight]);

  useEffect(() => {
    const host = stage.current;
    if (!host) {
      return undefined;
    }
    const onWheel = (event) => {
      event.preventDefault();
      zoomBy(event.deltaY < 0 ? 1.12 : 1 / 1.12, { x: event.clientX, y: event.clientY });
    };
    host.addEventListener("wheel", onWheel, { passive: false });
    return () => host.removeEventListener("wheel", onWheel);
  }, [drawingId, page]);

  return (
    <div className="drawing-lightbox" role="dialog" aria-label={`Drawing ${drawingId}`}>
      <div className="drawing-lightbox-bar">
        <p className="eyebrow">
          {drawingId} p.{page} · scroll to zoom · drag to move
        </p>
        <div className="drawing-tools">
          <button type="button" onClick={() => zoomBy(1 / 1.2)}>
            −
          </button>
          <button type="button" onClick={place}>
            Center
          </button>
          <button type="button" onClick={() => zoomBy(1.2)}>
            +
          </button>
          <button type="button" className="close" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      <div
        className="drawing-stage lightbox-stage"
        ref={stage}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { x: event.clientX - view.current.x, y: event.clientY - view.current.y };
        }}
        onPointerMove={(event) => {
          if (!drag.current) {
            return;
          }
          apply(view.current.scale, {
            x: event.clientX - drag.current.x,
            y: event.clientY - drag.current.y,
          });
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
      >
        <div
          className="drawing-world"
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})` }}
        >
          <img
            ref={image}
            alt={`${drawingId} page ${page}`}
            src={`/api/drawings/${drawingId}/page/${page}`}
            draggable={false}
            onLoad={place}
          />
          {highlight && (
            <div
              className="crop"
              style={{
                left: `${highlight.x * 100}%`,
                top: `${highlight.y * 100}%`,
                width: `${highlight.w * 100}%`,
                height: `${highlight.h * 100}%`,
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
