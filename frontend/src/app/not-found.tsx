import Link from "next/link";
export default function NotFound() {
  return <div className="p-4 muted">Asset not found. <Link href="/" className="amber">Back to screener</Link></div>;
}
