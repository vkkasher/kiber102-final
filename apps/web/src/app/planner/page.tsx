import Link from "next/link";
import { Planner } from "../../components/planner";
import { Shell, ArrowUpRight } from "../../components/ui";
export default function PlannerPage() {
  return (
    <Shell active="planner">
      <div className="inner-page planning-page">
        <div className="page-intro">
          <h1>
            День, который
            <br />
            запомнится
          </h1>
          <p>
            Соберите несколько событий в один маршрут.
            <br />
            Укажите время, компанию и общий бюджет.
          </p>
        </div>
        <div className="planning-layout">
          <aside className="planning-aside">
            <figure>
              <img
                src="/venues/bashopera.jpg"
                alt="Башкирский театр оперы и балета, Уфа"
                width="800"
                height="600"
              />
              <figcaption>Башкирский театр оперы и балета · Уфа</figcaption>
            </figure>
            <h2>
              У каждого дня
              <br />
              свой ритм
            </h2>
            <p>
              План учитывает расписание, возраст и стоимость билетов для всей
              компании. Если данных недостаточно, вы увидите, что нужно
              уточнить.
            </p>
            <Link href="/agent" className="text-button">
              Обсудить с помощником <ArrowUpRight size={16} />
            </Link>
          </aside>
          <Planner standalone />
        </div>
      </div>
    </Shell>
  );
}
