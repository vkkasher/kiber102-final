import { Shell, SourceLink } from "../../components/ui";

const photographs = [
  {
    title: "Башкирский театр оперы и балета",
    author: "Aysandlu",
    url: "https://commons.wikimedia.org/wiki/File:Оперный-2.jpg",
  },
  {
    title: "ГКЗ «Башкортостан»",
    author: "Vyacheslav Bukharov",
    url: "https://commons.wikimedia.org/wiki/File:Bashkortostan_Concert_Hall_in_Ufa_(March_2025).jpg",
  },
  {
    title: "Театр драмы имени М. Гафури",
    author: "Vyacheslav Bukharov",
    url: "https://commons.wikimedia.org/wiki/File:Views_of_Ufa_(March_2024)_-_11.jpg",
  },
  {
    title: "Русский драматический театр Башкортостана",
    author: "Svetlov Artem",
    url: "https://commons.wikimedia.org/wiki/File:Русский_академический_театр_драмы_Башкортостана_2022-05.jpg",
  },
];

export default function CreditsPage() {
  return (
    <Shell>
      <div className="inner-page credits-page">
        <div className="page-intro">
          <h1>Материалы и авторы</h1>
          <p>Источники изображений и условия использования.</p>
        </div>
        <div className="credits-list">
          {photographs.map((photo) => (
            <section key={photo.url}>
              <h2>{photo.title}</h2>
              <p>Автор: {photo.author}. Wikimedia Commons.</p>
              <p>
                <SourceLink href={photo.url}>Исходная фотография</SourceLink>
                {" · "}
                <SourceLink href="https://creativecommons.org/licenses/by-sa/4.0/">
                  CC BY-SA 4.0
                </SourceLink>
              </p>
              <p>В интерфейсе фотографии кадрируются под формат блока.</p>
            </section>
          ))}
          <section>
            <h2>Фотографии постановок</h2>
            <p>
              Изображения показаны с публичных страниц Башоперы, Русского театра
              Стерлитамака и Кассы.ру. Ссылка на страницу изображения и источник
              события указаны в его карточке. Права принадлежат соответствующим
              правообладателям.
            </p>
          </section>
          <section>
            <h2>Уточнение прав</h2>
            <p>
              Если вы представляете правообладателя и хотите уточнить подпись
              или использование материала, напишите через{" "}
              <a href="/feedback">обратную связь</a>.
            </p>
          </section>
        </div>
      </div>
    </Shell>
  );
}
