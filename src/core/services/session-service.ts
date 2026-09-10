import { Session } from '../models';

export class SessionService {
  private sessions: Map<string, Session> = new Map();

  createSession(sessionData: Omit<Session, 'id' | 'createdAt'>): Session {
    const session: Session = {
      id: this.generateId(),
      ...sessionData,
      createdAt: new Date()
    };

    this.sessions.set(session.id, session);
    return session;
  }

  getSession(id: string): Session | undefined {
    return this.sessions.get(id);
  }

  getAllSessions(): Session[] {
    return Array.from(this.sessions.values());
  }

  updateSession(id: string, updates: Partial<Session>): void {
    const session = this.getSession(id);
    if (session) {
      Object.assign(session, updates);
    }
  }

  deleteSession(id: string): boolean {
    return this.sessions.delete(id);
  }

  private generateId(): string {
    return Math.random().toString(36).substring(2, 15) + 
           Math.random().toString(36).substring(2, 15);
  }
}
