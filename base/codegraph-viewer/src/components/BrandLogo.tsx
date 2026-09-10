interface Props {
  size?: "sm" | "lg";
}

export default function BrandLogo({ size = "sm" }: Props) {
  const dim = size === "lg" ? "h-20 w-20" : "h-8 w-8";

  return (
    <img
      src="/jiuwen_logo.png"
      alt="Jiuwen"
      className={`${dim} shrink-0`}
    />
  );
}
