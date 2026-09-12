"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { createProject, listProjects } from "@/lib/api";
import type { Project } from "@/lib/types";

import styles from "./page.module.css";

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    void listProjects()
      .then((data) => {
        if (!active) {
          return;
        }
        setProjects(data);
      })
      .catch((err: unknown) => {
        if (!active) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load projects");
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  async function onCreateProject(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!name.trim()) {
      return;
    }

    try {
      setError(null);
      await createProject({ name: name.trim(), description: description.trim() || undefined });
      setName("");
      setDescription("");
      const data = await listProjects();
      setProjects(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create project");
    }
  }

  return (
    <div className={styles.page}>
      <main className={styles.main}>
        <div className={styles.intro}>
          <h1>Vellum</h1>
          <p>Project workspace foundation for Milestone 1.</p>
        </div>

        <form className={styles.form} onSubmit={(event) => void onCreateProject(event)}>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Project name"
            aria-label="Project name"
          />
          <input
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Description (optional)"
            aria-label="Project description"
          />
          <button type="submit">Create project</button>
        </form>

        {error ? <p className={styles.error}>{error}</p> : null}

        <section className={styles.listSection}>
          <h2>Projects</h2>
          {loading ? <p>Loading...</p> : null}
          {!loading && projects.length === 0 ? <p>No projects yet.</p> : null}
          <ul className={styles.list}>
            {projects.map((project) => (
              <li key={project.id}>
                <Link href={`/projects/${project.id}`}>{project.name}</Link>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  );
}
