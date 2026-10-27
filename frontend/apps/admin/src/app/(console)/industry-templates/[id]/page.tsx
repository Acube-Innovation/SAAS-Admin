import { IndustryEditorPage } from "@/components/industry/industry-editor";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <IndustryEditorPage id={id} />;
}
