// Three pulsing dots shown while an answer is being prepared. Screen readers hear the
// label instead; with "reduce motion" switched on the dots stay still (index.css).

interface Props {
  label?: string;
}

export function ThinkingDots({ label = "Thinking" }: Props) {
  return (
    <span role="status" aria-label={label} className="inline-flex items-center gap-1.5 py-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          aria-hidden
          className="thinking-dot h-2 w-2 rounded-full bg-indigo-500"
          style={{ animationDelay: `${i * 0.16}s` }}
        />
      ))}
    </span>
  );
}
