// Left panel: add images (upload or rescan the folder), pick one, and see or
// correct where it came from.
import { useRef, useState } from "react";

function basename(path) {
  return path.split("/").pop();
}

const PROVENANCE = [
  ["provider", "Provider"],
  ["acquired_at", "Captured on"],
  ["license", "License"],
  ["source_ref", "Source id"],
];

function Provenance({ image, onSave }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({});
  if (!editing) {
    return (
      <div className="box">
        <dl className="details">
          {PROVENANCE.map(([key, text]) => (
            <div key={key} style={{ display: "contents" }}>
              <dt>{text}</dt>
              <dd>{image[key] ?? <span className="muted">unknown</span>}</dd>
            </div>
          ))}
        </dl>
        <button onClick={() => {
          setDraft(Object.fromEntries(PROVENANCE.map(([k]) => [k, image[k] ?? ""])));
          setEditing(true);
        }}>Edit</button>
      </div>
    );
  }
  return (
    <form className="box" onSubmit={async (e) => {
      e.preventDefault();
      const fields = Object.fromEntries(
        Object.entries(draft).map(([k, v]) => [k, v.trim() === "" ? null : v.trim()])
      );
      if (await onSave(fields)) setEditing(false);
    }}>
      {PROVENANCE.map(([key, text]) => (
        <label key={key} className="field">
          {text}
          <input type={key === "acquired_at" ? "date" : "text"} value={draft[key]}
                 onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
        </label>
      ))}
      <div className="row">
        <button className="primary" type="submit">Save</button>
        <button type="button" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </form>
  );
}

export default function ImageList({ images, selectedId, onSelect, onUpload, onScan, onSaveProvenance }) {
  const fileInput = useRef(null);
  const selected = images.find((i) => i.image_id === selectedId);
  return (
    <>
      <div className="row">
        <button onClick={() => fileInput.current.click()}>Upload…</button>
        <button onClick={onScan} title="Register new files in data/images/">Rescan folder</button>
        <input ref={fileInput} type="file" accept=".tif,.tiff,.png" hidden
               onChange={(e) => {
                 const file = e.target.files?.[0];
                 e.target.value = "";
                 if (file) onUpload(file);
               }} />
      </div>
      {!images.length && (
        <p className="muted small">No images yet. Upload a GeoTIFF or PNG, or put files in
          data/images/ and rescan.</p>
      )}
      <ul className="image-list">
        {images.map((image) => (
          <li
            key={image.image_id}
            className={image.image_id === selectedId ? "selected" : ""}
            onClick={() => onSelect(image.image_id)}
          >
            <div className="name">{basename(image.source_uri)}</div>
            <div className="meta">
              {image.kind} · {image.crs ?? "pixel space"} · {image.width}×{image.height}
              {image.acquired_at ? ` · ${image.acquired_at}` : ""}
            </div>
          </li>
        ))}
      </ul>
      {selected && (
        <>
          <h2 style={{ marginTop: 16 }}>Where it came from</h2>
          <Provenance key={selected.image_id} image={selected}
                      onSave={(fields) => onSaveProvenance(selected.image_id, fields)} />
        </>
      )}
    </>
  );
}
