// A draggable edge between the map and a sidebar.
export default function Splitter({ onDrag, side }) {
  const start = (event) => {
    event.preventDefault();
    const move = (e) => onDrag(e.clientX);
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      document.body.classList.remove("resizing");
    };
    document.body.classList.add("resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };
  return <div className={`splitter ${side}`} onPointerDown={start}
              title="Drag to resize this panel" />;
}
