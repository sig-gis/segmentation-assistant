// A left-sidebar section that opens and closes by clicking its heading.
export default function Fold({ title, open, onToggle, children }) {
  return (
    <details className="fold" open={open}
             onToggle={(e) => { if (e.currentTarget.open !== open) onToggle(e.currentTarget.open); }}>
      <summary><h2>{title}</h2></summary>
      {children}
    </details>
  );
}
