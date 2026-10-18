import HomePageClient from "./HomePageClient";
import { fetchHomeData } from "./homeData";

export default async function HomePage() {
  const initialData = await fetchHomeData();
  return <HomePageClient initialData={initialData} />;
}
