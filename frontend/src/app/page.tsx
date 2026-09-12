import styles from "./page.module.css";

export default function Home() {
  return (
    <div className={styles.page}>
      <main className={styles.main}>
        <div className={styles.intro}>
          <h1>Vellum</h1>
          <p>
            Phase 0 foundation is running. Frontend scaffolding is ready for
            Milestone 1 implementation.
          </p>
        </div>
      </main>
    </div>
  );
}
