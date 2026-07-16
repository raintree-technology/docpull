type LogoMarkProps = {
  className?: string;
  title?: string;
};

export function LogoMark({ className, title }: LogoMarkProps) {
  const labelled = Boolean(title);

  return (
    <svg
      aria-hidden={labelled ? undefined : true}
      aria-label={title}
      className={className}
      role={labelled ? "img" : undefined}
      viewBox="0 0 64 64"
    >
      <path
        className="logo-mark-frame"
        d="M4 4h40l16 16v24L44 60H4V4Zm16 16v24h18l6-6V26l-6-6H20Z"
        fillRule="evenodd"
      />
      <path className="logo-mark-accent" d="M22 32h10v10H22z" />
    </svg>
  );
}
